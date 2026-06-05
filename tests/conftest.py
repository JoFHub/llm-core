"""Gemeinsame Fixtures für llm-core Tests."""
import pytest
from llm_core.config import LLMBackend, LLMConfig


@pytest.fixture
def ollama_config():
    return LLMConfig(backend=LLMBackend.OLLAMA, model="llama3.2:3b")


@pytest.fixture
def anthropic_config():
    return LLMConfig(backend=LLMBackend.ANTHROPIC, model="claude-sonnet-4-6")


@pytest.fixture
def openrouter_config():
    return LLMConfig(backend=LLMBackend.OPENROUTER, model="google/gemini-2.5-flash")
