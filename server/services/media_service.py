from pathlib import Path
from typing import Protocol
from uuid import uuid4


class UploadLike(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self) -> bytes: ...


UPLOAD_ROOT = Path(__file__).resolve().parent.parent.parent / "uploads"
IMAGE_DIR = UPLOAD_ROOT / "images"
VIDEO_DIR = UPLOAD_ROOT / "video"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)


async def save_upload_file(upload: UploadLike) -> dict:
    filename = (upload.filename or "upload.bin").strip()
    suffix = Path(filename).suffix or ".bin"
    mimetype = (upload.content_type or "").lower()
    target_dir = IMAGE_DIR if mimetype.startswith("image/") else VIDEO_DIR
    target_name = f"{uuid4().hex[:12]}{suffix}"
    target_path = target_dir / target_name
    content = await upload.read()
    target_path.write_bytes(content)
    rel = "images" if target_dir == IMAGE_DIR else "video"
    return {"url": f"/uploads/{rel}/{target_name}"}
