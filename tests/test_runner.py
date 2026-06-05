"""Tests für LLMRunner — ohne echte API-Calls."""
import pytest
from unittest.mock import MagicMock, patch

from llm_core.config import LLMBackend, LLMConfig
from llm_core.runner import LLMRunner


def test_privacy_mode_blocks_cloud(anthropic_config):
    cloud_with_privacy = LLMConfig(
        backend=LLMBackend.ANTHROPIC, model="claude-sonnet-4-6", privacy_mode=True
    )
    with pytest.raises(ValueError, match="Privacy Mode"):
        LLMRunner(cloud_with_privacy)


def test_privacy_mode_allows_ollama(ollama_config):
    config = LLMConfig(backend=LLMBackend.OLLAMA, model="llama3.2:3b", privacy_mode=True)
    runner = LLMRunner(config)
    assert runner.is_local
    assert runner.active_backend == LLMBackend.OLLAMA


def test_active_model_visible(ollama_config):
    runner = LLMRunner(ollama_config)
    assert runner.active_model == "llama3.2:3b"


def test_call_chat_delegates_to_gateway(ollama_config):
    runner = LLMRunner(ollama_config)
    runner._gateway = MagicMock()
    runner._gateway.chat.return_value = ("Hallo", "llama3.2:3b")

    result = runner.call_chat("Du bist ein Assistent.", "Hallo!")
    assert result == "Hallo"
    runner._gateway.chat.assert_called_once()


def test_call_single_parses_json(ollama_config):
    runner = LLMRunner(ollama_config)
    runner._gateway = MagicMock()
    runner._gateway.chat.return_value = ('{"name": "Test", "value": 42}', "llama3.2:3b")

    result = runner.call_single("Extrahiere Metadaten.", "Dokument: Test 42")
    assert result == {"name": "Test", "value": 42}


def test_call_single_retries_on_failure(ollama_config):
    config = LLMConfig(backend=LLMBackend.OLLAMA, model="llama3.2:3b")
    config.retry.retries = 2

    runner = LLMRunner(config)
    runner._gateway = MagicMock()
    runner._gateway.chat.side_effect = [
        RuntimeError("Timeout"),
        RuntimeError("Timeout"),
        ('{"ok": true}', "llama3.2:3b"),
    ]

    with patch("time.sleep"):
        result = runner.call_single("system", "user")
    assert result == {"ok": True}


def test_call_structured_raises_on_non_anthropic(ollama_config):
    from pydantic import BaseModel

    class MyModel(BaseModel):
        name: str

    runner = LLMRunner(ollama_config)
    with pytest.raises(NotImplementedError, match="AnthropicGateway"):
        runner.call_structured("system", "user", MyModel)


def test_json_repair_truncated():
    # Abgeschnittene schließende Klammer ist reparierbar
    repaired = LLMRunner._repair_truncated_json('{"name": "Test", "value": 42')
    import json
    data = json.loads(repaired)
    assert data.get("name") == "Test"
    assert data.get("value") == 42
