from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


def _static_file(static_dir: Path, path_name: str) -> Path:
    if not static_dir.exists():
        raise HTTPException(status_code=500, detail=f"static directory missing: {static_dir}")
    if not static_dir.is_dir():
        raise HTTPException(status_code=500, detail=f"static path is not a directory: {static_dir}")
    path = static_dir / path_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=500, detail=f"static file missing: {path}")
    return path


@router.get('/')
async def index():
    return FileResponse(_static_file(router.static_dir, 'index.html'))


@router.get('/broadcast')
async def broadcast():
    return FileResponse(_static_file(router.static_dir, 'broadcast.html'))


@router.get('/watch')
async def watch():
    return FileResponse(_static_file(router.static_dir, 'watch.html'))


@router.get('/gfs')
@router.get('/gfs/')
async def gfs_page():
    return FileResponse(_static_file(router.static_dir, 'indexgfs.html'))


@router.get('/status')
@router.get('/status-dashboard')
async def status_page():
    return FileResponse(_static_file(router.static_dir, 'status_dashboard.html'))
