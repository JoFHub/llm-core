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


def _track(model: str, in_tok: int, out_tok: int) -> None:
    try:
        from llm_core.cost_tracker import record
        record("anthropic", model, in_tok, out_tok)
    except Exception:
        pass


class AnthropicGateway(LLMGateway):
    def __init__(self, api_key: str | None = None) -> None:
        if not _AVAILABLE:
            raise ImportError("pip install anthropic")
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY nicht gesetzt.")
        self._client = _anthropic.Anthropic(api_key=key)

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

        msg = self._client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        _track(model_name, msg.usage.input_tokens, msg.usage.output_tokens)
        return msg.content[0].text, model_name

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
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        _track(model_name, msg.usage.input_tokens, msg.usage.output_tokens)
        return msg.content[0].text, model_name

    def chat_with_history(
        self,
        *,
        system: str,
        messages: list[dict],
        model_name: str,
        temperature: float,
        max_tokens: int,
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
            temperature=temperature,
            system=system,
            messages=to_anthropic_messages(messages),
        )
        _track(model_name, msg.usage.input_tokens, msg.usage.output_tokens)
        # content kann ein leerer Block sein (z.B. stop_reason ohne Text) —
        # Aufrufer erwarten einen String, kein IndexError.
        return msg.content[0].text if msg.content else ""

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
            kwargs["temperature"] = 1  # Pflicht wenn thinking aktiv
        else:
            kwargs["temperature"] = temperature

        response = self._client.messages.parse(
            model=model_name,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            output_format=output_type,
            **kwargs,
        )
        _track(model_name, response.usage.input_tokens, response.usage.output_tokens)
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
    ) -> tuple[str | None, list, list[dict]]:
        ant_messages = to_anthropic_messages(messages)
        ant_tools = [tool_to_anthropic(t) for t in tools]

        response = self._client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=ant_messages,
            tools=ant_tools,
        )
        _track(model_name, response.usage.input_tokens, response.usage.output_tokens)

        text, tool_calls = from_anthropic_response(response.content)

        assistant_msg: dict = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "input": tc.input} for tc in tool_calls
            ]

        return text, tool_calls, messages + [assistant_msg]
