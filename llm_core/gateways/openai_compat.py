"""OpenAI-kompatibler Gateway: OpenAI, OpenRouter, Mistral."""
from __future__ import annotations

import logging
import os

from .base import LLMGateway
from .._message_utils import tool_to_openai, to_openai_messages, from_openai_response

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


def _track(provider: str, model: str, usage) -> None:
    if not usage:
        return
    try:
        from llm_core.cost_tracker import record
        # OpenAI-Format (von OpenAI selbst automatisch, von Mistral bei
        # gesetztem prompt_cache_key): usage.prompt_tokens zaehlt Cache-Treffer
        # MIT -- anders als Anthropic (dort exklusive, s. gateways/anthropic.py)
        # muss der gecachte Anteil hier herausgerechnet werden, sonst würde er
        # doppelt verrechnet (einmal voll ueber prompt_tokens, einmal zu 10%
        # ueber cache_read_tokens).
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        record(
            provider, model,
            usage.prompt_tokens - cached, usage.completion_tokens,
            cache_read_tokens=cached,
        )
    except Exception:
        pass


def _cache_key_kwargs(provider: str, conversation_id: str | None) -> dict:
    """Mistral cached Prompt-Praefixe ueber einen stabilen `prompt_cache_key`
    (Top-Level-Requestfeld, kein Message-Format wie bei Anthropic/OpenRouter
    noetig) -- s. docs.mistral.ai/studio-api/conversations/advanced/prompt-caching.
    Andere Provider kennen das Feld nicht, deshalb nur fuer Mistral gesetzt."""
    if provider == "mistral" and conversation_id:
        return {"extra_body": {"prompt_cache_key": conversation_id}}
    return {}


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
        _track(self._provider, used_model, resp.usage)
        return resp.choices[0].message.content or "", used_model

    def chat_with_history(
        self,
        *,
        system: str,
        messages: list[dict],
        model_name: str,
        temperature: float,
        max_tokens: int,
        conversation_id: str | None = None,
    ) -> str:
        # to_openai_messages() ist fuer normale {"role": "user"/"assistant",
        # "content": str}-Historien ein No-Op, macht diese Methode aber auch fuer
        # Aufrufer sicher, die (wie call_agent()s max_iterations-Fallback) noch
        # das interne tool-Loop-Format uebergeben ({"input": dict} statt
        # {"function": {"arguments": json_str}}) — das lehnt die OpenAI-kompatible
        # API sonst mit 400 ab, weil bislang nur chat_with_tools() konvertiert hat.
        resp = self._client.chat.completions.create(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}] + to_openai_messages(messages),
            **_cache_key_kwargs(self._provider, conversation_id),
        )
        used_model = resp.model or model_name
        _track(self._provider, used_model, resp.usage)
        # content ist None, wenn der Provider (z.B. Gemini via OpenRouter) ohne
        # Tool-Angebot trotzdem nur einen leeren/verweigerten Turn liefert —
        # Aufrufer erwarten einen String, kein Optional.
        return resp.choices[0].message.content or ""

    def chat_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list,
        model_name: str,
        temperature: float,
        max_tokens: int,
        conversation_id: str | None = None,
    ) -> tuple[str | None, list, list[dict]]:
        oai_messages = [{"role": "system", "content": system}] + to_openai_messages(messages)
        oai_tools = [tool_to_openai(t) for t in tools]

        resp = self._client.chat.completions.create(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=oai_messages,
            tools=oai_tools,
            tool_choice="auto",
            **_cache_key_kwargs(self._provider, conversation_id),
        )
        used_model = resp.model or model_name
        _track(self._provider, used_model, resp.usage)

        text, tool_calls = from_openai_response(resp.choices[0])

        assistant_msg: dict = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "input": tc.input} for tc in tool_calls
            ]

        return text, tool_calls, messages + [assistant_msg]

    def chat_multimodal(
        self,
        *,
        system: str,
        user: str,
        images: list[dict],
        model_name: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str]:
        content: list[dict] = [{"type": "text", "text": user}]
        for img in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img['base64']}"},
            })
        resp = self._client.chat.completions.create(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        used_model = resp.model or model_name
        _track(self._provider, used_model, resp.usage)
        return resp.choices[0].message.content or "", used_model
