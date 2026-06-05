"""LLMConfig und RetryConfig — standalone, kein App-spezifischer Ballast."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class LLMBackend(str, Enum):
    OLLAMA     = "ollama"       # lokal, keine Cloud
    ANTHROPIC  = "anthropic"   # Claude (US)
    OPENROUTER = "openrouter"  # Aggregator (US), OpenAI-compat
    MISTRAL    = "mistral"     # EU, OpenAI-compat
    OPENAI     = "openai"      # OpenAI direkt (US)

    @property
    def is_local(self) -> bool:
        return self == LLMBackend.OLLAMA

    @property
    def region(self) -> str:
        return "EU" if self == LLMBackend.MISTRAL else ("local" if self.is_local else "US")


@dataclass
class RetryConfig:
    retries: int = 3
    base_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 30.0

    def sleep_time(self, attempt: int) -> float:
        return min(self.base_delay * math.pow(self.backoff_factor, attempt), self.max_delay)

    @classmethod
    def from_dict(cls, d: dict) -> "RetryConfig":
        return cls(
            retries=d.get("retries", 3),
            base_delay=d.get("base_delay", 1.0),
            backoff_factor=d.get("backoff_factor", 2.0),
            max_delay=d.get("max_delay", 30.0),
        )


@dataclass
class LLMConfig:
    backend: LLMBackend
    model: str
    temperature: float = 0.1
    max_tokens: int = 4096
    retry: RetryConfig = field(default_factory=RetryConfig)
    ollama_host: str = "http://localhost:11434"
    openai_base_url: str | None = None
    openai_api_key_env: str = "OPENAI_API_KEY"
    privacy_mode: bool = False
    cost_db_path: Path = field(default_factory=lambda: Path("~/.llm_core/costs.sqlite"))

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Lädt Konfiguration aus Umgebungsvariablen."""
        backend_str = os.getenv("LLM_BACKEND", "ollama").lower()
        try:
            backend = LLMBackend(backend_str)
        except ValueError:
            raise ValueError(
                f"Unbekanntes LLM_BACKEND='{backend_str}'. "
                f"Gültig: {[b.value for b in LLMBackend]}"
            )

        model = os.getenv("LLM_MODEL")
        if not model:
            _defaults = {
                LLMBackend.OLLAMA: "llama3.2:3b",
                LLMBackend.ANTHROPIC: "claude-sonnet-4-6",
                LLMBackend.OPENROUTER: "google/gemini-2.5-flash",
                LLMBackend.MISTRAL: "mistral-small-latest",
                LLMBackend.OPENAI: "gpt-4o-mini",
            }
            model = _defaults[backend]

        return cls(
            backend=backend,
            model=model,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
            openai_api_key_env=os.getenv("LLM_API_KEY_ENV", "OPENAI_API_KEY"),
            privacy_mode=os.getenv("LLM_PRIVACY_MODE", "").lower() in ("1", "true", "yes"),
        )

    @classmethod
    def from_dict(cls, d: dict) -> "LLMConfig":
        """Lädt Konfiguration aus einem Dict (z.B. aus JSON-Config-Datei)."""
        backend = LLMBackend(d["backend"])
        retry = RetryConfig.from_dict(d.get("retry", {}))
        return cls(
            backend=backend,
            model=d["model"],
            temperature=d.get("temperature", 0.1),
            max_tokens=d.get("max_tokens", 4096),
            retry=retry,
            ollama_host=d.get("ollama_host", "http://localhost:11434"),
            openai_base_url=d.get("openai_base_url") or None,
            openai_api_key_env=d.get("openai_api_key_env", "OPENAI_API_KEY"),
            privacy_mode=d.get("privacy_mode", False),
        )
