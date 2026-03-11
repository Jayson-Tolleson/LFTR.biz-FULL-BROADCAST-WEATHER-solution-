from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Generator

from .auth import get_effective_google_project, maybe_apply_google_credentials_env, resolve_gcp_auth_mode


log = logging.getLogger("server.ai.gemini")


@dataclass
class StubProvider:
    def generate_content(self, prompt: str) -> str:
        prompt = (prompt or "").strip()
        if not prompt:
            return "I didn't catch that."
        return f"AI: Based on current feed, {prompt}"

    def stream_content(self, prompt: str) -> Generator[str, None, None]:
        yield self.generate_content(prompt)


class VertexGeminiProvider:
    def __init__(self) -> None:
        maybe_apply_google_credentials_env()
        self.project = get_effective_google_project()
        self.location = os.getenv("VERTEX_LOCATION", "global").strip() or "global"
        self.model_name = os.getenv("VERTEX_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

        from vertexai import init
        from vertexai.generative_models import GenerativeModel

        init(project=self.project, location=self.location)
        self.model = GenerativeModel(self.model_name)

    def generate_content(self, prompt: str) -> str:
        response = self.model.generate_content(prompt)
        return (getattr(response, "text", "") or "").strip()

    def stream_content(self, prompt: str) -> Generator[str, None, None]:
        for chunk in self.model.generate_content(prompt, stream=True):
            text = (getattr(chunk, "text", "") or "")
            if text:
                yield text


_PROVIDER = None
_PROVIDER_KIND = "stub"


def _vertex_requested() -> bool:
    ai_provider = os.getenv("AI_PROVIDER", "").strip().lower()
    if ai_provider == "vertex":
        return True
    return bool(get_effective_google_project() and resolve_gcp_auth_mode() in {"adc_ok", "explicit_key_ok"})


def _get_provider():
    global _PROVIDER, _PROVIDER_KIND
    if _PROVIDER is not None:
        return _PROVIDER

    if _vertex_requested():
        try:
            _PROVIDER = VertexGeminiProvider()
            _PROVIDER_KIND = "vertex"
            log.info("[AI] Vertex Gemini enabled")
            log.info("[AI] Model: %s", _PROVIDER.model_name)
            log.info("[AI] Location: %s", _PROVIDER.location)
            return _PROVIDER
        except Exception:
            log.warning("[AI] Vertex credentials unavailable auth_mode=%s — AI disabled", resolve_gcp_auth_mode())

    _PROVIDER = StubProvider()
    _PROVIDER_KIND = "stub"
    return _PROVIDER


def provider_name() -> str:
    _get_provider()
    return _PROVIDER_KIND


def generate_ai_reply(text: str) -> dict:
    provider = _get_provider()
    prompt = (text or "").strip()
    reply = provider.generate_content(prompt)
    if not reply:
        reply = "I didn't catch that."

    from server.ai.speech import synthesize_voice

    return {"text": reply, "voice": synthesize_voice(reply), "provider": _PROVIDER_KIND}


async def stream_ai_reply(text: str):
    provider = _get_provider()
    prompt = (text or "").strip()
    if not prompt:
        yield "I didn't catch that."
        return

    def _collect_tokens() -> list[str]:
        return list(provider.stream_content(prompt))

    tokens = await asyncio.to_thread(_collect_tokens)
    if not tokens:
        fallback = provider.generate_content(prompt)
        if fallback:
            yield fallback
        return

    for token in tokens:
        yield token
