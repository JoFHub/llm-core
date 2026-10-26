"""Gemeinsame Fixtures für llm-core Tests."""
import pytest
from llm_core.config import LLMBackend, LLMConfig


@pytest.fixture(autouse=True)
def _kosten_db_isoliert(tmp_path, monkeypatch):
    """Jeder Test schreibt in eine eigene Kosten-DB, nie in ~/.llm_core/costs.sqlite.
    Vorher landeten Gateway-Tests mit MagicMock-Kosten (float(MagicMock) = 1.0) als
    Scheinkosten in der echten Datenbank (ca. 33 USD bis 2026-10-25)."""
    monkeypatch.setenv("LLM_CORE_DATA_DIR", str(tmp_path))


@pytest.fixture
def ollama_config():
    return LLMConfig(backend=LLMBackend.OLLAMA, model="llama3.2:3b")


@pytest.fixture
def anthropic_config():
    return LLMConfig(backend=LLMBackend.ANTHROPIC, model="claude-sonnet-4-6")


@pytest.fixture
def openrouter_config():
    return LLMConfig(backend=LLMBackend.OPENROUTER, model="google/gemini-2.5-flash")
