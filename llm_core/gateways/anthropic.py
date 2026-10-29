"""Anthropic Gateway — Claude API mit Thinking Mode und Structured Output."""
from __future__ import annotations

import logging
import os
from typing import TypeVar

from pydantic import BaseModel

from .base import LLMGateway
from .._message_utils import (
    tool_to_anthropic, to_anthropic_messages, from_anthropic_response,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

try:
    import anthropic as _anthropic
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _track(model: str, usage) -> None:
    try:
        from llm_core.cost_tracker import record
        record(
            "anthropic", model,
            usage.input_tokens, usage.output_tokens,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
    except Exception:
        pass


def _cacheable_system(system: str) -> str | list[dict]:
    """System-Prompt als eigenen Cache-Breakpoint markieren -- identisch ueber
    alle Aufrufe desselben Profils/derselben Konversation hinweg, klassischer
    Cache-Kandidat. Unterhalb der Mindestlaenge (Claude: 1024 Tokens fuer
    Sonnet/Opus) ignoriert die API cache_control folgenlos, kein Nachteil."""
    if not system:
        return system
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _mark_last_message_cacheable(messages: list[dict]) -> list[dict]:
    """Cache-Breakpoint auf die letzte Nachricht -- Standardmuster fuer
    Multi-Turn-Caching: der Praefix bis zur vorherigen Bruchstelle wurde im
    letzten Request an genau dieser (dann vorletzten) Position bereits
    geschrieben und wird hier aus dem Server-Cache gelesen, sofern er
    byte-identisch ist und die 5-Minuten-TTL noch laeuft."""
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


def _mark_last_tool_cacheable(tools: list[dict]) -> list[dict]:
    """Ein Breakpoint auf den letzten Toolschema-Eintrag cached die gesamte
    (statische, oft grosse) Tool-Liste -- wirkt ueber alle Runden eines
    Agent-Loops sowie ueber Folgeturns derselben Konversation hinweg."""
    if not tools:
        return tools
    tools = [dict(t) for t in tools]
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def _text(msg) -> str:
    """Alle Textbloecke der Antwort -- bei Modellen mit Denkspur (Opus 5.5,
    Sonnet 5.5, Haiku 5.5 denken immer) steht vorn ein thinking-Block, dort
    brach msg.content[0].text ab."""
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


class AnthropicGateway(LLMGateway):
    # `temperature` wird nicht an die API weitergereicht: seit anthropic-sdk 1.x
    # kennt Messages.create() diesen Parameter nicht mehr (ersetzt durch
    # `output_config.effort`). Bleibt in den Methodensignaturen fuer die
    # gemeinsame LLMGateway-Schnittstelle, wird hier aber ignoriert.
    def __init__(self, api_key: str | None = None, effort: str | None = None) -> None:
        if not _AVAILABLE:
            raise ImportError("pip install anthropic")
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY nicht gesetzt.")
        self._client = _anthropic.Anthropic(api_key=key)
        # output_config.effort (low … max), None = Modell-Standard; steuert
        # Denktiefe und damit, wie viel von max_tokens die Denkspur verbraucht
        self._effort = effort

    def _effort_kwargs(self) -> dict:
        return {"output_config": {"effort": self._effort}} if self._effort else {}

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
        user_content = user
        if json_mode:
            user_content += "\n\nAntworte ausschließlich mit gültigem JSON, ohne Erklärungen."

        # Streaming: lange Antworten (ein Romankapitel plus Denkspur) brauchen
        # mehr als die ~21.000 Tokens, die das SDK ohne Streaming zulaesst
        with self._client.messages.stream(
            model=model_name,
            max_tokens=max_tokens,
            system=_cacheable_system(system),
            messages=[{"role": "user", "content": user_content}],
            **self._effort_kwargs(),
        ) as stream:
            msg = stream.get_final_message()
        _track(model_name, msg.usage)
        if msg.stop_reason == "max_tokens":
            logger.warning("Antwort von %s bei max_tokens=%s abgeschnitten", model_name, max_tokens)
        return _text(msg), model_name

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
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img["base64"]},
            })
        msg = self._client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            system=_cacheable_system(system),
            messages=[{"role": "user", "content": content}],
            **self._effort_kwargs(),
        )
        _track(model_name, msg.usage)
        return _text(msg), model_name

    def chat_with_history(
        self,
        *,
        system: str,
        messages: list[dict],
        model_name: str,
        temperature: float,
        max_tokens: int,
        conversation_id: str | None = None,  # ungenutzt: cache_control-Breakpoints statt Cache-Key
    ) -> str:
        # to_anthropic_messages() ist fuer normale {"role": "user"/"assistant",
        # "content": str}-Historien ein No-Op, macht diese Methode aber auch fuer
        # Aufrufer sicher, die (wie call_agent()s max_iterations-Fallback) noch
        # das interne tool-Loop-Format mit role="tool"/tool_calls uebergeben —
        # das lehnt die Anthropic-API sonst mit "Unexpected role" ab, weil nur
        # chat_with_tools() bislang konvertiert hat.
        msg = self._client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            system=_cacheable_system(system),
            messages=_mark_last_message_cacheable(to_anthropic_messages(messages)),
            **self._effort_kwargs(),
        )
        _track(model_name, msg.usage)
        # content kann leer sein (z.B. stop_reason ohne Text) -- Aufrufer
        # erwarten einen String, kein IndexError.
        return _text(msg)

    def chat_structured(
        self,
        *,
        system: str,
        user: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        output_type: type[T],
        thinking: bool = False,
    ) -> T:
        kwargs: dict = {}
        if thinking:
            kwargs["thinking"] = {"type": "adaptive"}

        response = self._client.messages.parse(
            model=model_name,
            system=_cacheable_system(system),
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            output_format=output_type,
            **kwargs,
        )
        _track(model_name, response.usage)
        return response.parsed_output

    def chat_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list,
        model_name: str,
        temperature: float,
        max_tokens: int,
        conversation_id: str | None = None,  # ungenutzt: cache_control-Breakpoints statt Cache-Key
    ) -> tuple[str | None, list, list[dict]]:
        ant_messages = _mark_last_message_cacheable(to_anthropic_messages(messages))
        ant_tools = _mark_last_tool_cacheable([tool_to_anthropic(t) for t in tools])

        response = self._client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            system=_cacheable_system(system),
            messages=ant_messages,
            tools=ant_tools,
        )
        _track(model_name, response.usage)

        text, tool_calls = from_anthropic_response(response.content)

        assistant_msg: dict = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "input": tc.input} for tc in tool_calls
            ]

        return text, tool_calls, messages + [assistant_msg]
