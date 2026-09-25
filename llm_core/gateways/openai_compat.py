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

        if provider == "openrouter":
            # OpenRouter fuehrt zusaetzlich cache_write_tokens und einen
            # bereits fertig berechneten Gesamtpreis `cost` (beides Felder,
            # die openai-SDK selbst nicht kennt, aber dank extra="allow"
            # trotzdem per Attribut erreichbar sind -- live gegen die echte
            # API verifiziert, s. docs/journal.md). Die dokumentierten
            # Rabatt-Multiplikatoren unterscheiden sich je Unterbau-Provider
            # stark (Anthropic 0,1x/1,25x, Google 0,25x, OpenAI 0,25-0,5x) --
            # ein fixer Multiplikator waere hier falsch. `usage.cost` ist der
            # bereits von OpenRouter selbst kalkulierte Ist-Preis inkl.
            # Cache-Rabatt, wird deshalb direkt als Override verwendet statt
            # ihn ueber Multiplikator-Schaetzung nachzubauen. Fehlt das Feld
            # (Response-Format-Aenderung), faellt record() automatisch auf
            # die Schaetzung zurueck (cost_usd_override=None).
            cache_write = getattr(details, "cache_write_tokens", 0) or 0
            total_cost = getattr(usage, "cost", None)
            override = float(total_cost) if total_cost is not None else None
            record(
                provider, model,
                usage.prompt_tokens - cached, usage.completion_tokens,
                cache_creation_tokens=cache_write,
                cache_read_tokens=cached,
                cost_usd_override=override,
            )
            return

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
    OpenRouter kennt kein prompt_cache_key, aber `session_id` fuers Provider-
    Sticky-Routing (haelt Folge-Requests derselben Konversation beim selben
    Unterbau-Provider, erhoeht die Trefferquote der cache_control-Breakpoints
    unten) -- s. openrouter.ai/docs/features/prompt-caching. Beides als
    extra_body, da kein Standardparameter der openai-SDK."""
    if provider == "mistral" and conversation_id:
        return {"extra_body": {"prompt_cache_key": conversation_id}}
    if provider == "openrouter" and conversation_id:
        return {"extra_body": {"session_id": conversation_id}}
    return {}


def _cacheable_system_or(system: str) -> str | list[dict]:
    """System-Prompt als Cache-Breakpoint markieren -- identische Syntax wie
    gateways/anthropic.py::_cacheable_system(), da OpenRouter fuer Anthropic-/
    Gemini-/Qwen-Modelle dieselbe cache_control-Konvention durchreicht. Fuer
    Modelle mit automatischem Caching (OpenAI, DeepSeek, ...) laut Doku
    folgenlos ignoriert."""
    if not system:
        return system
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _mark_last_message_cacheable_or(messages: list[dict]) -> list[dict]:
    """Cache-Breakpoint auf die letzte Nachricht -- identische Logik wie
    gateways/anthropic.py::_mark_last_message_cacheable()."""
    if not messages:
        return messages
    messages = list(messages)
    last = dict(messages[-1])
    content = last.get("content")
    cache_control = {"type": "ephemeral"}
    if isinstance(content, str) and content:
        last["content"] = [{"type": "text", "text": content, "cache_control": cache_control}]
        messages[-1] = last
    elif isinstance(content, list) and content:
        content = [dict(b) for b in content]
        content[-1] = {**content[-1], "cache_control": cache_control}
        last["content"] = content
        messages[-1] = last
    return messages


def _mark_last_tool_cacheable_or(tools: list[dict]) -> list[dict]:
    """Breakpoint auf den letzten Tool-Schema-Eintrag cached die gesamte
    (statische) Tool-Liste -- gleiches Prinzip wie gateways/anthropic.py::
    _mark_last_tool_cacheable(), hier im OpenAI-Toolformat (type=function)."""
    if not tools:
        return tools
    tools = [dict(t) for t in tools]
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


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
        oai_messages = to_openai_messages(messages)
        system_content: str | list[dict] = system
        if self._provider == "openrouter":
            system_content = _cacheable_system_or(system)
            oai_messages = _mark_last_message_cacheable_or(oai_messages)

        resp = self._client.chat.completions.create(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system_content}] + oai_messages,
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
        history_messages = to_openai_messages(messages)
        oai_tools = [tool_to_openai(t) for t in tools]
        system_content: str | list[dict] = system
        if self._provider == "openrouter":
            system_content = _cacheable_system_or(system)
            history_messages = _mark_last_message_cacheable_or(history_messages)
            oai_tools = _mark_last_tool_cacheable_or(oai_tools)
        oai_messages = [{"role": "system", "content": system_content}] + history_messages

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
