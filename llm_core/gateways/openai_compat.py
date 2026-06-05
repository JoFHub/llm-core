"""OpenAI-kompatibler Gateway: OpenAI, OpenRouter, Mistral."""
from __future__ import annotations

import logging
import os

from .base import LLMGateway

logger = logging.getLogger(__name__)

# Vorkonfigurierte Endpunkte für bekannte Provider
_PROVIDER_DEFAULTS: dict[str, dict] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
    },
    "openai": {
        "base_url": None,
        "api_key_env": "OPENAI_API_KEY",
    },
}

try:
    import openai as _openai
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _track(provider: str, model: str, in_tok: int, out_tok: int) -> None:
    try:
        from llm_core.cost_tracker import record
        record(provider, model, in_tok, out_tok)
    except Exception:
        pass


class OpenAICompatGateway(LLMGateway):
    def __init__(
        self,
        provider: str = "openai",
        base_url: str | None = None,
        api_key_env: str | None = None,
        api_key: str | None = None,
    ) -> None:
        if not _AVAILABLE:
            raise ImportError("pip install openai")

        self._provider = provider
        defaults = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["openai"])
        resolved_base_url = base_url or defaults["base_url"]
        resolved_key_env = api_key_env or defaults["api_key_env"]

        key = api_key or os.getenv(resolved_key_env)
        if not key:
            raise ValueError(
                f"API Key für '{provider}' nicht gefunden. "
                f"Setze die Umgebungsvariable {resolved_key_env}."
            )

        self._client = _openai.OpenAI(api_key=key, base_url=resolved_base_url)

    def chat(
        self,
        *,
        system: str,
        user: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> tuple[str, str]:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        used_model = resp.model or model_name
        _track(self._provider, used_model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
        return resp.choices[0].message.content, used_model

    def chat_with_history(
        self,
        *,
        system: str,
        messages: list[dict],
        model_name: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        resp = self._client.chat.completions.create(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}] + messages,
        )
        used_model = resp.model or model_name
        _track(self._provider, used_model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
        return resp.choices[0].message.content
