"""Tests für call_agent und Tool-Use-Loop."""
from unittest.mock import MagicMock, patch
import pytest

from llm_core import LLMRunner, LLMConfig, LLMBackend, Tool, AgentResult
from llm_core.agent import ToolCall


@pytest.fixture
def search_tool():
    return Tool(
        name="search",
        description="Sucht in der Datenbank.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def _make_runner():
    return LLMRunner(LLMConfig(backend=LLMBackend.OLLAMA, model="llama3.2:3b"))


def test_agent_no_tools_called(search_tool):
    runner = _make_runner()
    runner._gateway = MagicMock()
    runner._gateway.chat_with_tools.return_value = (
        "Die Antwort ist 42.", [], [{"role": "user", "content": "Frage"}]
    )

    result = runner.call_agent(
        system="Du bist ein Assistent.",
        user="Was ist die Antwort?",
        tools=[search_tool],
        tool_executor=lambda name, inp: "",
    )

    assert result.answer == "Die Antwort ist 42."
    assert result.steps == []
    runner._gateway.chat_with_tools.assert_called_once()


def test_agent_one_tool_call(search_tool):
    runner = _make_runner()
    runner._gateway = MagicMock()

    tc = ToolCall(id="call_1", name="search", input={"query": "Python"})

    runner._gateway.chat_with_tools.side_effect = [
        # Erste Runde: Tool aufrufen
        (None, [tc], [{"role": "user", "content": "Frage"}, {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "name": "search", "input": {"query": "Python"}}]}]),
        # Zweite Runde: Antwort
        ("Python ist eine Programmiersprache.", [], []),
    ]

    tool_executor = MagicMock(return_value="Python: eine Programmiersprache.")

    result = runner.call_agent(
        system="Du bist ein Assistent.",
        user="Was ist Python?",
        tools=[search_tool],
        tool_executor=tool_executor,
    )

    assert result.answer == "Python ist eine Programmiersprache."
    assert len(result.steps) == 1
    assert result.steps[0].tool_name == "search"
    assert result.steps[0].tool_result == "Python: eine Programmiersprache."
    tool_executor.assert_called_once_with("search", {"query": "Python"})


def test_agent_max_iterations(search_tool):
    runner = _make_runner()
    runner._gateway = MagicMock()

    tc = ToolCall(id="call_1", name="search", input={"query": "loop"})
    # Immer Tool-Calls zurückgeben → max_iterations auslösen
    runner._gateway.chat_with_tools.return_value = (
        None, [tc], [{"role": "assistant", "tool_calls": [{"id": "call_1", "name": "search", "input": {}}]}]
    )
    runner._gateway.chat_with_history = MagicMock(return_value="Abbruch-Antwort.")

    result = runner.call_agent(
        system="System.",
        user="Frage?",
        tools=[search_tool],
        tool_executor=lambda n, i: "result",
        max_iterations=3,
    )

    assert runner._gateway.chat_with_tools.call_count == 3
    assert result.answer == "Abbruch-Antwort."
    assert len(result.steps) == 3


def test_agent_prior_messages(search_tool):
    runner = _make_runner()
    runner._gateway = MagicMock()
    runner._gateway.chat_with_tools.return_value = ("Antwort.", [], [])

    prior = [{"role": "user", "content": "Vorherige Frage"}, {"role": "assistant", "content": "Vorherige Antwort"}]

    runner.call_agent(
        system="System.",
        user="Neue Frage.",
        tools=[search_tool],
        tool_executor=lambda n, i: "",
        prior_messages=prior,
    )

    call_args = runner._gateway.chat_with_tools.call_args
    messages_passed = call_args.kwargs["messages"]
    assert messages_passed[0]["content"] == "Vorherige Frage"
    assert messages_passed[-1]["content"] == "Neue Frage."


def test_message_utils_openai_roundtrip():
    from llm_core._message_utils import to_openai_messages
    messages = [
        {"role": "user", "content": "Hallo"},
        {"role": "assistant", "content": "Hi", "tool_calls": [
            {"id": "c1", "name": "search", "input": {"q": "test"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "search", "content": "Ergebnis"},
    ]
    oai = to_openai_messages(messages)
    assert oai[0] == {"role": "user", "content": "Hallo"}
    assert oai[1]["role"] == "assistant"
    assert oai[1]["tool_calls"][0]["function"]["name"] == "search"
    assert oai[2]["role"] == "tool"
    assert oai[2]["tool_call_id"] == "c1"


def test_message_utils_anthropic_groups_tool_results():
    from llm_core._message_utils import to_anthropic_messages
    messages = [
        {"role": "user", "content": "Frage"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "name": "search", "input": {}},
            {"id": "c2", "name": "lookup", "input": {}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "search", "content": "R1"},
        {"role": "tool", "tool_call_id": "c2", "name": "lookup", "content": "R2"},
    ]
    ant = to_anthropic_messages(messages)
    # Tool-Ergebnisse müssen in EINER user-Nachricht gebündelt sein
    tool_result_msg = ant[-1]
    assert tool_result_msg["role"] == "user"
    assert len(tool_result_msg["content"]) == 2
    assert tool_result_msg["content"][0]["type"] == "tool_result"
