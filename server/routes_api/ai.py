from quart import current_app, request
from fastapi import APIRouter
from server.services import ai_service
from server.ai.gemini import provider_name
from server.ai import ai_status as get_ai_status

router = APIRouter()


@router.get('/status')
async def ai_status():
    settings = current_app.state.settings
    ai_provider = provider_name()
    auth = get_ai_status()
    ai_available = bool(settings.ai_enabled and ai_provider != 'stub')
    return {
        "ai_available": ai_available,
        "ai_enabled": settings.ai_enabled,
        "provider": ai_provider,
        "tts_available": ai_available,
        "stt_available": bool(auth.get("stt_ready")),
        "google_auth": auth,
    }


@router.post('/chat')
async def ai_chat():
    settings = current_app.state.settings
    payload = await request.get_json(force=True)
    return await ai_service.handle_chat(payload, settings.ai_fallback_text)


@router.post('/tts')
async def ai_tts():
    from server.ai.speech import synthesize_voice

    payload = await request.get_json(force=True)
    text = str((payload or {}).get('text') or '').strip()
    if not text:
        return {'ok': False, 'error': 'missing_text', 'message': 'text is required', 'data': None}
    voice = synthesize_voice(text)
    return {'ok': True, 'provider': provider_name(), 'error': None, 'message': 'ok', 'data': {'voice': voice}}


@router.post('/websearch')
async def ai_websearch():
    payload = await request.get_json(force=True)
    return await ai_service.handle_websearch(payload)
