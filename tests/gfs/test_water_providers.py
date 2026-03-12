from __future__ import annotations

import json
import urllib.parse

from server.gfs.models import BBox
from server.gfs.providers.coastwatch import CoastwatchProvider
from server.gfs.providers.rtofs import RtofsProvider


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_rtofs_salvages_noaa_currents_and_erddap_sst_patterns(monkeypatch):
    seen = []

    def _urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        seen.append(url)
        if 'datagetter' in url:
            payload = {"current_predictions": [{"Velocity_Major": "1.3", "Direction_Bin": "45"}]}
            return _Resp(json.dumps(payload).encode())
        if 'erddap' in url:
            return _Resp(b'time,latitude,longitude,sst\n2024-01-01T00:00:00Z,34,-120,20.5\n')
        return _Resp(b'{}')

    monkeypatch.setattr('urllib.request.urlopen', _urlopen)

    provider = RtofsProvider()
    data, _ = provider._fetch_subset_sync(bbox=BBox(-121, 33, -119, 35), stride=4, valid_time=None)
    assert 'sst' in data and 'current_u' in data and 'current_v' in data
    assert any('product=currents_predictions' in u for u in seen)
    assert any('erddap' in u for u in seen)


def test_coastwatch_returns_chlorophyll_and_water_color(monkeypatch):
    def _urlopen(req, timeout=0):
        _ = timeout
        return _Resp(b'time,latitude,longitude,chlorophyll\n2024-01-01T00:00:00Z,34,-120,1.2\n')

    monkeypatch.setattr('urllib.request.urlopen', _urlopen)

    provider = CoastwatchProvider()
    data, _ = provider._fetch_subset_sync(bbox=BBox(-121, 33, -119, 35), stride=4, valid_time=None)
    assert 'chlorophyll' in data
    assert 'water_color_index' in data


def test_rtofs_health_reports_live_upstreams():
    payload = RtofsProvider().health()
    assert payload['status'] == 'live_with_fallback'
    assert 'noaa_coops_currents' in payload['upstreams']
