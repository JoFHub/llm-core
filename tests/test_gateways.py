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


def test_openai_compat_mistral_sends_prompt_cache_key():
    from llm_core.gateways.openai_compat import OpenAICompatGateway

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Antwort"))]
        mock_response.model = "mistral-large-latest"
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 10
        mock_response.usage.prompt_tokens_details = None
        mock_client.chat.completions.create.return_value = mock_response

        gateway = OpenAICompatGateway(provider="mistral", api_key="dummy")
        gateway.chat_with_history(
            system="s", messages=[{"role": "user", "content": "Frage"}],
            model_name="mistral-large-latest", temperature=0.1, max_tokens=100,
            conversation_id="conv-42",
        )

        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"] == {"prompt_cache_key": "conv-42"}


def test_openai_compat_non_mistral_ignores_conversation_id():
    from llm_core.gateways.openai_compat import OpenAICompatGateway

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Antwort"))]
        mock_response.model = "openrouter-model"
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 10
        mock_response.usage.prompt_tokens_details = None
        mock_client.chat.completions.create.return_value = mock_response

        gateway = OpenAICompatGateway(provider="openrouter", api_key="dummy")
        gateway.chat_with_history(
            system="s", messages=[{"role": "user", "content": "Frage"}],
            model_name="openrouter-model", temperature=0.1, max_tokens=100,
            conversation_id="conv-42",
        )

        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "extra_body" not in kwargs


def test_openai_compat_subtracts_cached_tokens_before_billing():
    """usage.prompt_tokens zaehlt Cache-Treffer im OpenAI-Format MIT -- ungekuerzt
    an cost_tracker.record() durchgereicht wuerde der gecachte Anteil doppelt
    verrechnet (einmal voll, einmal nochmal zu 10% als cache_read_tokens)."""
    from llm_core.gateways.openai_compat import OpenAICompatGateway

    with patch("openai.OpenAI") as MockOpenAI, \
         patch("llm_core.cost_tracker.record") as mock_record:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Antwort"))]
        mock_response.model = "mistral-large-latest"
        mock_response.usage.prompt_tokens = 1013
        mock_response.usage.completion_tokens = 20
        mock_response.usage.prompt_tokens_details.cached_tokens = 1008
        mock_client.chat.completions.create.return_value = mock_response

        gateway = OpenAICompatGateway(provider="mistral", api_key="dummy")
        gateway.chat_with_history(
            system="s", messages=[{"role": "user", "content": "Frage"}],
            model_name="mistral-large-latest", temperature=0.1, max_tokens=100,
        )

        mock_record.assert_called_once_with(
            "mistral", "mistral-large-latest", 5, 20, cache_read_tokens=1008,
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


def test_anthropic_chat_with_history_sets_cache_breakpoints():
    """System-Prompt und letzte Nachricht muessen als Cache-Breakpoints
    markiert sein -- sonst wird die wachsende History bei jedem Turn erneut
    zum vollen Preis abgerechnet (s. docs/journal.md, 2026-09-09)."""
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
            system="Systemprompt",
            messages=[
                {"role": "user", "content": "Erste Frage"},
                {"role": "assistant", "content": "Erste Antwort"},
                {"role": "user", "content": "Zweite Frage"},
            ],
            model_name="claude-test", temperature=0.1, max_tokens=100,
        )

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["system"][0]["text"] == "Systemprompt"

        sent = kwargs["messages"]
        # nur die letzte Nachricht traegt den Breakpoint, aeltere bleiben
        # unveraendert (einfacher String statt Block-Liste)
        assert sent[0]["content"] == "Erste Frage"
        assert sent[1]["content"] == "Erste Antwort"
        assert sent[2]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert sent[2]["content"][0]["text"] == "Zweite Frage"


def test_anthropic_chat_with_tools_sets_cache_breakpoints():
    from llm_core.agent import Tool
    from llm_core.gateways.anthropic import AnthropicGateway

    with patch("anthropic.Anthropic") as MockAnthropic:
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = []
        mock_response.usage.input_tokens = 1
        mock_response.usage.output_tokens = 1
        mock_client.messages.create.return_value = mock_response

        gateway = AnthropicGateway(api_key="dummy")
        tools = [
            Tool(name="tool_a", description="A", parameters={"type": "object", "properties": {}}),
            Tool(name="tool_b", description="B", parameters={"type": "object", "properties": {}}),
        ]
        gateway.chat_with_tools(
            system="Systemprompt",
            messages=[{"role": "user", "content": "Frage"}],
            tools=tools,
            model_name="claude-test", temperature=0.1, max_tokens=100,
        )

        kwargs = mock_client.messages.create.call_args.kwargs
        sent_tools = kwargs["tools"]
        assert "cache_control" not in sent_tools[0]
        assert sent_tools[1]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["messages"][-1]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_track_forwards_cache_tokens_to_cost_tracker():
    from llm_core.gateways.anthropic import AnthropicGateway

    with patch("anthropic.Anthropic") as MockAnthropic, \
         patch("llm_core.cost_tracker.record") as mock_record:
        mock_client = MagicMock()
        MockAnthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Antwort")]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        mock_response.usage.cache_creation_input_tokens = 200
        mock_response.usage.cache_read_input_tokens = 300
        mock_client.messages.create.return_value = mock_response

        gateway = AnthropicGateway(api_key="dummy")
        gateway.chat_with_history(
            system="s", messages=[{"role": "user", "content": "Frage"}],
            model_name="claude-test", temperature=0.1, max_tokens=100,
        )

        mock_record.assert_called_once_with(
            "anthropic", "claude-test", 10, 5,
            cache_creation_tokens=200, cache_read_tokens=300,
        )


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
