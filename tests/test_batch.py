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




# ── OpenRouter ────────────────────────────────────────────────────────────────

def _or_runner(effort="medium"):
    from llm_core import LLMBackend, LLMConfig, LLMRunner
    return LLMRunner(LLMConfig(backend=LLMBackend.OPENROUTER, model="openai/gpt-6.1-terra", max_tokens=64000,
                               api_key="dummy", effort=effort, temperature=0.9))


def _or_fertig(text="Kapitel", finish="stop"):
    # Wie die echte Antwort (Probe 2026-11-19): cost nur in der usage des Batches, nicht in der des body
    return {"id": "batch_1", "status": "completed", "request_counts": {"total": 1, "completed": 1, "failed": 0},
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.0016, "is_byok": False},
            "results": [{"id": "batch_req_1", "custom_id": "kapitel-1", "error": None, "response": {
                "status_code": 200, "request_id": "r1", "body": {
                    "model": "openai/gpt-6.1-terra",
                    "choices": [{"message": {"content": text}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 500,
                              "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                              "completion_tokens_details": {"reasoning_tokens": 100}}}}}]}


def test_openrouter_batch_abschicken():
    with patch("openai.OpenAI") as MockOpenAI:
        client = MockOpenAI.return_value
        client.post.return_value = {"id": "batch_1", "status": "validating"}
        runner = _or_runner()
        assert runner.kann_batch
        assert runner.batch_abschicken("sys", "u", custom_id="schreiben-1") == "batch_1"
        pfad, = client.post.call_args.args
        body = client.post.call_args.kwargs["body"]
        assert pfad == "/batches"
        assert list(body)[:2] == ["endpoint", "model"]  # vor requests, sonst 400
        assert (body["endpoint"], body["model"]) == ("/v1/chat/completions", "openai/gpt-6.1-terra")
        anfrage = body["requests"][0]
        assert anfrage["custom_id"] == "schreiben-1"
        b = anfrage["body"]
        assert b["reasoning"] == {"effort": "medium"} and "temperature" not in b and b["max_tokens"] == 64000
        assert b["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert b["messages"][1] == {"role": "user", "content": "u"}


def test_openrouter_batch_ergebnis():
    from llm_core.cost_tracker import total_cost
    with patch("openai.OpenAI") as MockOpenAI:
        client = MockOpenAI.return_value
        runner = _or_runner(effort=None)
        client.get.return_value = {"id": "batch_1", "status": "in_progress"}
        assert runner.batch_ergebnis("batch_1") is None
        client.get.return_value = _or_fertig(finish="length")
        e = runner.batch_ergebnis("batch_1")
        assert (e.art, e.text, e.stop_reason) == ("succeeded", "Kapitel", "length")
        assert e.usage.prompt_tokens == 1000 and e.usage.completion_tokens_details.reasoning_tokens == 100
        assert e.usage.cost == 0.0016
        assert total_cost(days=1) == pytest.approx(0.0016)  # Ist-Preis von OpenRouter, schon zum Batch-Preis
        client.get.return_value = {"id": "batch_1", "status": "expired", "error": None, "results": []}
        assert runner.batch_ergebnis("batch_1").art == "expired"
        fehler = _or_fertig()
        fehler["results"][0].update(response=None, error={"type": "x", "message": "kaputt"})
        client.get.return_value = fehler
        e = runner.batch_ergebnis("batch_1")
        assert (e.art, e.fehler) == ("errored", "kaputt")


def test_kein_batch_bei_mistral():
    from llm_core import LLMBackend, LLMConfig, LLMRunner
    with patch("openai.OpenAI"):
        runner = LLMRunner(LLMConfig(backend=LLMBackend.MISTRAL, model="m", api_key="d"))
    assert not runner.kann_batch
