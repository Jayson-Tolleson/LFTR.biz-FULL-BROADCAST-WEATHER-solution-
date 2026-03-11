from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from quart import websocket

from server.gfs.bbox import normalize_bbox
from server.gfs.cache import TinyTTLCache
from server.gfs.config import GfsConfig
from server.gfs.derive.bait import derive_bait_payload
from server.gfs.derive.clouds import derive_cloud_layers
from server.gfs.derive.weather import derive_weather_fields
from server.gfs.errors import InvalidBBoxError, ProviderUnavailableError
from server.gfs.models import BAIT_ATMOSPHERIC_VARIABLES, CLOUD_VARIABLES, RequestIntent, WEATHER_VARIABLES
from server.gfs.providers.coastwatch import CoastwatchProvider
from server.gfs.providers.rtofs import RtofsProvider
from server.gfs.providers.thredds_gfs import ThreddsGfsProvider
from server.gfs.serializers import serialize_bait, serialize_clouds, serialize_weather
from server.gfs.snapshot import SnapshotState


log = logging.getLogger("server.gfs.engine")


class GfsEngine:
    """Orchestrates bounded subset fetches, derivation, and compact JSON payloads."""

    def __init__(self, config: GfsConfig) -> None:
        self.config = config
        self.atmospheric = ThreddsGfsProvider(config.thredds_best_url)
        self.rtofs = RtofsProvider()
        self.coastwatch = CoastwatchProvider()
        self.cache = TinyTTLCache()
        self.snapshot = SnapshotState(payloads={})
        self._ws_clients: set[Any] = set()
        self._lock = asyncio.Lock()

    def parse_intent(self, query: dict[str, str]) -> RequestIntent:
        raw_bbox = query.get("bbox")
        if not raw_bbox:
            raise InvalidBBoxError("bbox query param is required")
        quality = query.get("quality", "auto")
        if quality not in {"auto", "full", "medium", "coarse"}:
            raise InvalidBBoxError("quality must be auto|full|medium|coarse")
        valid_time = None
        if query.get("valid_time"):
            valid_time = datetime.fromisoformat(query["valid_time"].replace("Z", "+00:00")).replace(tzinfo=None)
        pad = float(query.get("pad", str(self.config.default_pad)))
        normalized = normalize_bbox(raw_bbox, pad=pad, max_pad=self.config.max_pad, max_cells=self.config.max_cells)
        stride = normalized.stride
        if quality == "full":
            stride = 1
        elif quality == "medium":
            stride = max(stride, 2)
        elif quality == "coarse":
            stride = max(stride, 4)
        debug = query.get("debug") == "1"
        if debug and not self.config.debug_enabled:
            raise InvalidBBoxError("debug=1 is only allowed in debug mode")
        return RequestIntent(
            bbox=normalized.merged,
            bboxes=normalized.parts,
            pad=pad,
            quality=quality,
            stride=stride,
            valid_time=valid_time,
            debug=debug,
        )

    async def weather_payload(self, intent: RequestIntent) -> dict[str, Any]:
        key = f"weather:{intent.bbox.as_list()}:{intent.valid_time}:{intent.stride}"
        cached = self.cache.get(key)
        if cached:
            return cached
        raw, valid_time, stale = await self._fetch_atmospheric(WEATHER_VARIABLES, intent, snapshot_key="weather")
        payload = serialize_weather(
            valid_time=valid_time,
            bbox=intent.bbox.as_list(),
            stride=intent.stride,
            fields=derive_weather_fields(raw),
            stale=stale,
        )
        self.cache.set(key, payload)
        return payload

    async def clouds_payload(self, intent: RequestIntent) -> dict[str, Any]:
        raw, valid_time, stale = await self._fetch_atmospheric(CLOUD_VARIABLES, intent, snapshot_key="clouds")
        layers, convective = derive_cloud_layers(raw)
        return serialize_clouds(valid_time=valid_time, bbox=intent.bbox.as_list(), layers=layers, convective=convective, stale=stale)

    async def bait_payload(self, intent: RequestIntent) -> dict[str, Any]:
        atmospheric, valid_time, stale = await self._fetch_atmospheric(BAIT_ATMOSPHERIC_VARIABLES, intent, snapshot_key="bait")
        ocean, _ = await self.rtofs.fetch_subset(bbox=intent.bbox, stride=intent.stride, valid_time=intent.valid_time)
        bio, _ = await self.coastwatch.fetch_subset(bbox=intent.bbox, stride=intent.stride, valid_time=intent.valid_time)
        derived = derive_bait_payload(atmospheric, ocean, bio)
        return serialize_bait(valid_time=valid_time, bbox=intent.bbox.as_list(), stale=stale, **derived)

    async def _fetch_atmospheric(self, variables: tuple[str, ...], intent: RequestIntent, snapshot_key: str) -> tuple[dict[str, Any], datetime | None, bool]:
        merged: dict[str, Any] = {}
        source_time = intent.valid_time
        try:
            async with self._lock:
                for box in intent.bboxes:
                    data, source_time = await self.atmospheric.fetch_subset(
                        variables=variables,
                        bbox=box,
                        stride=intent.stride,
                        valid_time=intent.valid_time,
                    )
                    merged.update(data)
            self.snapshot.mark_success(source_time)
            if self.snapshot.payloads is not None:
                self.snapshot.payloads[snapshot_key] = merged
            return merged, source_time, False
        except ProviderUnavailableError as exc:
            self.snapshot.mark_degraded()
            stale_payload = self.snapshot.payloads.get(snapshot_key) if self.snapshot.payloads else None
            if stale_payload:
                log.warning("provider unavailable; serving stale snapshot key=%s err=%s", snapshot_key, exc)
                return stale_payload, self.snapshot.last_valid_time, True
            log.error("provider unavailable with no stale snapshot key=%s err=%s", snapshot_key, exc)
            raise

    def health_payload(self) -> dict[str, Any]:
        return {
            "providers": {
                "thredds_gfs": self.atmospheric.health(),
                "rtofs": self.rtofs.health(),
                "coastwatch": self.coastwatch.health(),
            },
            "last_successful_fetch": self.snapshot.last_successful_fetch.isoformat() + "Z" if self.snapshot.last_successful_fetch else None,
            "last_valid_data_time": self.snapshot.last_valid_time.isoformat() + "Z" if self.snapshot.last_valid_time else None,
            "degraded": self.snapshot.degraded,
            "cache": self.cache.stats(),
            "dataset_urls": {"thredds_best": self.config.thredds_best_url},
        }

    async def websocket_handler(self) -> None:
        ws = websocket._get_current_object()
        self._ws_clients.add(ws)
        try:
            await ws.accept()
            await ws.send_json({"type": "connected", "channel": "gfs_control"})
            while True:
                await ws.receive()
        finally:
            self._ws_clients.discard(ws)

    async def broadcast_control(self, event_type: str, detail: dict[str, Any] | None = None) -> None:
        msg = {"type": event_type, "detail": detail or {}}
        dead = []
        for ws in self._ws_clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_clients.discard(ws)
