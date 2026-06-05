"""Tests für Gateway-Schicht — ohne echte API-Calls."""
import json
import pytest
from unittest.mock import MagicMock, patch


def test_ollama_gateway_builds_correct_payload():
    from llm_core.gateways.ollama import OllamaGateway

    gateway = OllamaGateway(host="http://localhost:11434")
    captured = {}

    def mock_urlopen(req, timeout=None):
        captured["data"] = json.loads(req.data.decode())
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"message": {"content": "Antwort"}}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", mock_urlopen):
        content, model = gateway.chat(
            system="Du bist ein Assistent.",
            user="Hallo",
            model_name="llama3.2:3b",
            temperature=0.1,
            max_tokens=512,
            json_mode=True,
        )

    assert content == "Antwort"
    assert model == "llama3.2:3b"
    assert captured["data"]["format"] == "json"
    assert captured["data"]["messages"][0]["role"] == "system"


def test_ollama_gateway_raises_on_connection_error():
    from llm_core.gateways.ollama import OllamaGateway
    import urllib.error

    gateway = OllamaGateway(host="http://localhost:11434")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with pytest.raises(RuntimeError, match="Ollama nicht erreichbar"):
            gateway.chat(
                system="s", user="u", model_name="m",
                temperature=0.1, max_tokens=100,
            )


def test_openai_compat_provider_defaults():
    from llm_core.gateways.openai_compat import _PROVIDER_DEFAULTS
    assert "openrouter" in _PROVIDER_DEFAULTS
    assert "mistral" in _PROVIDER_DEFAULTS
    assert _PROVIDER_DEFAULTS["mistral"]["base_url"] == "https://api.mistral.ai/v1"
    assert _PROVIDER_DEFAULTS["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"
