"""Privacy-Enforcement: erzwingt lokale Ausführung (Ollama)."""
from __future__ import annotations

import warnings
from dataclasses import replace

from .config import LLMBackend, LLMConfig


def with_privacy(config: LLMConfig, local_model: str | None = None) -> LLMConfig:
    """
    Gibt eine Kopie der Config zurück, die ausschließlich Ollama verwendet.

    Args:
        config: Ausgangs-Konfiguration.
        local_model: Optionales Ollama-Modell. Falls None, wird config.model beibehalten
                     (nur sinnvoll wenn es bereits ein Ollama-Modell ist).
    """
    if config.backend != LLMBackend.OLLAMA:
        warnings.warn(
            f"Privacy Mode: Backend '{config.backend.value}' ({config.backend.region}) "
            f"wird auf Ollama (lokal) umgestellt. Kein Cloud-Call findet statt.",
            stacklevel=2,
        )

    overrides: dict = {"backend": LLMBackend.OLLAMA, "privacy_mode": True}
    if local_model:
        overrides["model"] = local_model

    return replace(config, **overrides)
