"""Anthropic Gateway — Claude API mit Thinking Mode und Structured Output."""
from __future__ import annotations

import logging
import os
from typing import TypeVar

from pydantic import BaseModel

from .base import LLMGateway

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
        msg = self._client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
        )
        _track(model_name, msg.usage.input_tokens, msg.usage.output_tokens)
        return msg.content[0].text

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
        kwargs: dict = {"temperature": temperature}
        if thinking:
            kwargs["thinking"] = {"type": "adaptive"}

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
