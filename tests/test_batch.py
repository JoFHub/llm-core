"""Tests: Message Batches im Anthropic-Gateway (halber Preis) -- ohne echte API-Calls."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _usage(ein=1000, aus=500):
    return SimpleNamespace(input_tokens=ein, output_tokens=aus, cache_creation_input_tokens=0,
                           cache_read_input_tokens=0)


def _runner(effort="medium"):
    from llm_core import LLMBackend, LLMConfig, LLMRunner
    return LLMRunner(LLMConfig(backend=LLMBackend.ANTHROPIC, model="claude-sonnet-4-6", max_tokens=64000,
                               api_key="dummy", effort=effort))


def test_batch_halber_preis():
    from llm_core.cost_tracker import record, total_cost
    record("anthropic", "claude-sonnet-4-6", 1000, 500)
    voll = total_cost(days=1)
    record("anthropic", "claude-sonnet-4-6", 1000, 500, batch=True)
    assert total_cost(days=1) == pytest.approx(voll * 1.5)


def test_batch_abschicken_mit_effort_und_cache():
    with patch("anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        client.messages.batches.create.return_value = SimpleNamespace(id="msgbatch_1")
        runner = _runner()
        assert runner.kann_batch
        assert runner.batch_abschicken("sys", "u", custom_id="kapitel-3") == "msgbatch_1"
        anfrage = client.messages.batches.create.call_args.kwargs["requests"][0]
        assert anfrage["custom_id"] == "kapitel-3"
        p = anfrage["params"]
        assert (p["model"], p["max_tokens"], p["output_config"]) == ("claude-sonnet-4-6", 64000, {"effort": "medium"})
        assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert p["messages"] == [{"role": "user", "content": "u"}]


def test_batch_ergebnis_laeuft_fertig_und_fehler():
    from llm_core.cost_tracker import total_cost
    with patch("anthropic.Anthropic") as MockAnthropic:
        client = MockAnthropic.return_value
        runner = _runner(effort=None)
        client.messages.batches.retrieve.return_value = SimpleNamespace(processing_status="in_progress")
        assert runner.batch_ergebnis("b") is None
        assert total_cost(days=1) == 0

        client.messages.batches.retrieve.return_value = SimpleNamespace(processing_status="ended")
        msg = SimpleNamespace(model="claude-sonnet-4-6", usage=_usage(), stop_reason="end_turn",
                              content=[SimpleNamespace(type="thinking", thinking="…"),
                                       SimpleNamespace(type="text", text="Kapitel")])
        client.messages.batches.results.return_value = [
            SimpleNamespace(custom_id="anfrage", result=SimpleNamespace(type="succeeded", message=msg))]
        e = runner.batch_ergebnis("b")
        assert (e.art, e.text, e.stop_reason) == ("succeeded", "Kapitel", "end_turn")
        assert total_cost(days=1) > 0

        client.messages.batches.results.return_value = [
            SimpleNamespace(custom_id="anfrage", result=SimpleNamespace(type="expired"))]
        assert runner.batch_ergebnis("b").art == "expired"


def test_kein_batch_bei_openrouter():
    from llm_core import LLMBackend, LLMConfig, LLMRunner
    with patch("openai.OpenAI"):
        runner = LLMRunner(LLMConfig(backend=LLMBackend.OPENROUTER, model="x", api_key="d"))
    assert not runner.kann_batch
    with pytest.raises(NotImplementedError):
        runner.batch_abschicken("s", "u")
