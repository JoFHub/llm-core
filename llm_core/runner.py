"""LLMRunner — einheitlicher Entrypoint für alle LLM-Calls."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, TypeVar

from pydantic import BaseModel

from .config import LLMBackend, LLMConfig
from .gateways.ollama import OllamaGateway

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

try:
    from .gateways.anthropic import AnthropicGateway
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    from .gateways.openai_compat import OpenAICompatGateway
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


class LLMRunner:
    """Einheitlicher LLM-Client mit Retry, JSON-Repair und Privacy-Enforcement."""

    def __init__(self, config: LLMConfig) -> None:
        if config.privacy_mode and config.backend != LLMBackend.OLLAMA:
            raise ValueError(
                f"Privacy Mode aktiv, aber Backend ist '{config.backend.value}'. "
                "Privacy Mode erlaubt nur lokale Modelle (Ollama). "
                "Verwende LLMBackend.OLLAMA oder deaktiviere privacy_mode."
            )

        self._config = config

        if config.backend == LLMBackend.OLLAMA:
            self._gateway = OllamaGateway(host=config.ollama_host)

        elif config.backend == LLMBackend.ANTHROPIC:
            if not _ANTHROPIC_AVAILABLE:
                raise ImportError("pip install anthropic")
            self._gateway = AnthropicGateway()

        elif config.backend in (LLMBackend.OPENROUTER, LLMBackend.MISTRAL, LLMBackend.OPENAI):
            if not _OPENAI_AVAILABLE:
                raise ImportError("pip install openai")
            self._gateway = OpenAICompatGateway(
                provider=config.backend.value,
                base_url=config.openai_base_url,
                api_key_env=config.openai_api_key_env or None,
            )
        else:
            raise ValueError(f"Unbekanntes Backend: {config.backend}")

    @property
    def active_model(self) -> str:
        return self._config.model

    @property
    def active_backend(self) -> LLMBackend:
        return self._config.backend

    @property
    def is_local(self) -> bool:
        return self._config.backend.is_local

    # ------------------------------------------------------------------
    # JSON Parsing / Repair
    # ------------------------------------------------------------------

    @staticmethod
    def _clean(text: str) -> str:
        if not text:
            raise ValueError("Leere LLM-Antwort.")
        text = text.strip()
        if "```" in text:
            for char in ["{", "["]:
                idx = text.find(char)
                if idx != -1:
                    end_char = "}" if char == "{" else "]"
                    end_idx = text.rfind(end_char)
                    if end_idx != -1:
                        return text[idx:end_idx + 1]
        for char in ["{", "["]:
            idx = text.find(char)
            if idx != -1:
                end_char = "}" if char == "{" else "]"
                end_idx = text.rfind(end_char)
                if end_idx != -1:
                    return text[idx:end_idx + 1]
        return text.strip()

    @staticmethod
    def _repair_truncated_json(text: str) -> str:
        text = text.strip().rstrip(",")
        open_braces = text.count("{") - text.count("}")
        open_brackets = text.count("[") - text.count("]")
        if text.count('"') % 2 != 0:
            text += '"'
        text += "}" * open_braces
        text += "]" * open_brackets
        return text

    @staticmethod
    def _parse_single(text: str) -> dict[str, Any]:
        cleaned = LLMRunner._clean(text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            repaired = LLMRunner._repair_truncated_json(cleaned)
            data = json.loads(repaired)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    return item
        raise ValueError(f"Erwartete Dict, erhielt {type(data)}")

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def call_chat(self, system: str, user: str, json_mode: bool = False) -> str:
        """Einfacher Text-Chat → roher String."""
        response, _ = self._gateway.chat(
            system=system, user=user,
            json_mode=json_mode,
            model_name=self._config.model,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        return response

    def call_single(self, system: str, user: str) -> dict[str, Any]:
        """LLM-Aufruf → JSON-Dict (z.B. Metadaten-Extraktion). Mit Retry."""
        last_exc: Exception | None = None
        for attempt in range(self._config.retry.retries + 1):
            try:
                response, _ = self._gateway.chat(
                    system=system, user=user,
                    json_mode=(self._config.backend != LLMBackend.ANTHROPIC),
                    model_name=self._config.model,
                    temperature=self._config.temperature,
                    max_tokens=self._config.max_tokens,
                )
                return self._parse_single(response)
            except Exception as e:
                last_exc = e
                if attempt < self._config.retry.retries:
                    delay = self._config.retry.sleep_time(attempt)
                    logger.warning(f"[{self._config.model}] Retry {attempt + 1}: {e} (warte {delay:.1f}s)")
                    time.sleep(delay)
        raise RuntimeError(f"LLM-Aufruf fehlgeschlagen nach {self._config.retry.retries + 1} Versuchen") from last_exc

    def call_chat_with_history(self, system: str, messages: list[dict]) -> str:
        """Chat mit vollständiger Konversationshistorie."""
        if hasattr(self._gateway, "chat_with_history"):
            return self._gateway.chat_with_history(
                system=system,
                messages=messages,
                model_name=self._config.model,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
            )
        # Fallback: letzten User-Turn verwenden
        user_msg = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        return self.call_chat(system=system, user=user_msg)

    def call_structured(
        self,
        system: str,
        user: str,
        output_type: type[T],
        thinking: bool = False,
    ) -> T:
        """
        Strukturierter Output via Pydantic-Modell.
        Nur für AnthropicGateway verfügbar (Claude).
        """
        if not hasattr(self._gateway, "chat_structured"):
            raise NotImplementedError(
                f"call_structured ist nur mit AnthropicGateway verfügbar, "
                f"aktives Backend: {self._config.backend.value}"
            )
        return self._gateway.chat_structured(
            system=system,
            user=user,
            model_name=self._config.model,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            output_type=output_type,
            thinking=thinking,
        )
