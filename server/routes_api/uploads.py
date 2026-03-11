from quart import request
from fastapi import APIRouter
from server.media.upload import save_upload

router = APIRouter(prefix='/api')


@router.post('/upload')
async def api_upload():
    files = await request.files
    file_storage = files.get('file')
    if not file_storage:
        return {"error": "file required"}, 400
    return await save_upload(file_storage)
