"""Abstrakte Basisklasse für alle LLM-Gateways."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMGateway(ABC):
    @abstractmethod
    def chat(
        self,
        *,
        system: str,
        user: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> tuple[str, str]: ...

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
        raise NotImplementedError(f"{type(self).__name__} unterstützt kein Multimodal.")

    def chat_with_history(
        self,
        *,
        system: str,
        messages: list[dict],
        model_name: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        raise NotImplementedError(f"{type(self).__name__} unterstützt keine Message-History.")

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
        raise NotImplementedError(
            f"{type(self).__name__} unterstützt kein Structured Output. "
            "Verwende AnthropicGateway."
        )
