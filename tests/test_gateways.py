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


def test_ollama_gateway_strips_think_block():
    """Reasoning-Modelle (z.B. deepseek-r1) liefern die Denkspur roh im
    content mit -- Ollama trennt das nur bei Modellen mit nativer Support."""
    from llm_core.gateways.ollama import OllamaGateway

    gateway = OllamaGateway(host="http://localhost:11434")

    def mock_urlopen(req, timeout=None):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"message": {"content": "<think>Lass mich überlegen...</think>\n\nDie Antwort ist 42."}}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", mock_urlopen):
        content, _ = gateway.chat(
            system="s", user="u", model_name="deepseek-r1:14b",
            temperature=0.1, max_tokens=512,
        )

    assert content == "Die Antwort ist 42."
    assert "<think>" not in content


def test_ollama_gateway_strips_unclosed_think_block():
    """Bei max_tokens abgeschnittene Denkspur ohne schliessendes Tag."""
    from llm_core.gateways.ollama import OllamaGateway

    gateway = OllamaGateway(host="http://localhost:11434")

    def mock_urlopen(req, timeout=None):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"message": {"content": "<think>Noch am Ueberlegen, wurde abgeschnitten"}}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", mock_urlopen):
        content, _ = gateway.chat(
            system="s", user="u", model_name="deepseek-r1:14b",
            temperature=0.1, max_tokens=512,
        )

    assert content == ""


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


# call_agent()s max_iterations-Fallback ruft chat_with_history() mit dem
# internen tool-Loop-Nachrichtenformat auf (role="tool", tool_calls als
# {"input": dict}) statt mit einer einfachen user/assistant-Historie. Ohne
# Konvertierung lehnten Anthropic ("Unexpected role tool") und OpenAI-
# kompatible Backends (falsches tool_calls-Schema) das mit 400 ab.
_INTERNAL_TOOL_LOOP_MESSAGES = [
    {"role": "user", "content": "Frage"},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "name": "search_documents", "input": {"query": "x"}},
    ]},
    {"role": "tool", "tool_call_id": "call_1", "name": "search_documents", "content": "Ergebnis"},
]


def test_anthropic_chat_with_history_converts_tool_loop_messages():
    from llm_core.gateways.anthropic import AnthropicGateway

    with patch("anthropic.Anthropic") as MockAnthropic:
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Antwort")]
        mock_response.usage.input_tokens = 1
        mock_response.usage.output_tokens = 1
        mock_client.messages.create.return_value = mock_response

        gateway = AnthropicGateway(api_key="dummy")
        gateway.chat_with_history(
            system="s", messages=_INTERNAL_TOOL_LOOP_MESSAGES,
            model_name="claude-test", temperature=0.1, max_tokens=100,
        )

        sent = mock_client.messages.create.call_args.kwargs["messages"]
        roles = [m["role"] for m in sent]
        assert "tool" not in roles
        assert roles == ["user", "assistant", "user"]


def test_openai_compat_chat_with_history_converts_tool_loop_messages():
    from llm_core.gateways.openai_compat import OpenAICompatGateway

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Antwort"))]
        mock_response.model = "mistral-test"
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1
        mock_client.chat.completions.create.return_value = mock_response

        gateway = OpenAICompatGateway(provider="mistral", api_key="dummy")
        gateway.chat_with_history(
            system="s", messages=_INTERNAL_TOOL_LOOP_MESSAGES,
            model_name="mistral-test", temperature=0.1, max_tokens=100,
        )

        sent = mock_client.chat.completions.create.call_args.kwargs["messages"]
        assistant_msgs = [m for m in sent if m.get("role") == "assistant" and "tool_calls" in m]
        assert len(assistant_msgs) == 1
        tc = assistant_msgs[0]["tool_calls"][0]
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "search_documents"
        assert json.loads(tc["function"]["arguments"]) == {"query": "x"}


# Manche Provider (u.a. Gemini via OpenRouter) liefern message.content=None
# zurueck statt eines leeren Strings, z.B. bei einem verweigerten/leeren Turn.
# call_agent()s max_iterations-Fallback reichte das bisher ungeprueft als
# AgentResult.answer durch -> Aufrufer (memoria._guard_setext_underlines)
# stuerzte mit 'NoneType' object has no attribute 'split' ab.
def test_openai_compat_chat_with_history_handles_none_content():
    from llm_core.gateways.openai_compat import OpenAICompatGateway

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=None))]
        mock_response.model = "mistral-test"
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 0
        mock_client.chat.completions.create.return_value = mock_response

        gateway = OpenAICompatGateway(provider="mistral", api_key="dummy")
        result = gateway.chat_with_history(
            system="s", messages=[{"role": "user", "content": "Frage"}],
            model_name="mistral-test", temperature=0.1, max_tokens=100,
        )
        assert result == ""


def test_anthropic_chat_with_history_handles_empty_content():
    from llm_core.gateways.anthropic import AnthropicGateway

    with patch("anthropic.Anthropic") as MockAnthropic:
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = []
        mock_response.usage.input_tokens = 1
        mock_response.usage.output_tokens = 0
        mock_client.messages.create.return_value = mock_response

        gateway = AnthropicGateway(api_key="dummy")
        result = gateway.chat_with_history(
            system="s", messages=[{"role": "user", "content": "Frage"}],
            model_name="claude-test", temperature=0.1, max_tokens=100,
        )
        assert result == ""
