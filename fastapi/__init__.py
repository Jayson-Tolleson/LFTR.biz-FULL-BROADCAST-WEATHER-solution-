from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from quart import Quart, request as quart_request, websocket as quart_websocket


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def Depends(fn: Callable):
    return fn


class UploadFile:
    def __init__(self, filename: str = "", content_type: str = "", body: bytes = b""):
        self.filename = filename
        self.content_type = content_type
        self._body = body

    async def read(self) -> bytes:
        return self._body


class File:
    def __init__(self, default: Any = ...):
        self.default = default


a = Any
Request = Any
WebSocket = Any


@dataclass
class _Route:
    method: str
    path: str
    fn: Callable


class APIRouter:
    def __init__(self, prefix: str = ""):
        self.prefix = prefix
        self.routes: list[_Route] = []

    def get(self, path: str):
        def deco(fn):
            self.routes.append(_Route("GET", path, fn))
            return fn

        return deco

    def post(self, path: str):
        def deco(fn):
            self.routes.append(_Route("POST", path, fn))
            return fn

        return deco

    def websocket(self, path: str):
        def deco(fn):
            self.routes.append(_Route("WS", path, fn))
            return fn

        return deco


class FastAPI(Quart):
    def __init__(self, *args, **kwargs):
        _ = kwargs.pop("title", None)
        _ = kwargs.pop("version", None)
        name = kwargs.pop("name", __name__)
        super().__init__(name, *args, **kwargs)
        self.state = SimpleNamespace()

    def include_router(self, router: APIRouter, prefix: str = ""):
        base = f"{prefix}{router.prefix}"
        for route in router.routes:
            full = f"{base}{route.path}"
            if route.method == "GET":
                self.get(full)(route.fn)
            elif route.method == "POST":
                self.post(full)(route.fn)
            elif route.method == "WS":
                self.websocket(full)(route.fn)

    def mount(self, path: str, app: Any, name: str | None = None):
        _ = (name,)
        directory = getattr(app, "directory", None)

        if directory:
            @self.get(f"{path}/<path:filename>")
            async def _static_file(filename: str, _dir=directory):
                from quart import send_from_directory

                return await send_from_directory(_dir, filename)


__all__ = [
    "FastAPI",
    "APIRouter",
    "Depends",
    "HTTPException",
    "UploadFile",
    "File",
    "Request",
    "WebSocket",
]
