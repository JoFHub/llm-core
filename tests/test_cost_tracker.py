"""Tests für Cost Tracker."""
import os
import tempfile
import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CORE_DATA_DIR", str(tmp_path))


def test_record_and_total():
    from llm_core.cost_tracker import record, total_cost
    record("anthropic", "claude-sonnet-4-6", 1000, 500)
    cost = total_cost(days=1)
    assert cost > 0


def test_daily_summary():
    from llm_core.cost_tracker import record, daily_summary
    record("anthropic", "claude-sonnet-4-6", 1000, 500)
    summary = daily_summary(days=1)
    assert len(summary) == 1
    assert summary[0]["prompt_tokens"] == 1000


def test_model_summary():
    from llm_core.cost_tracker import record, model_summary
    record("anthropic", "claude-sonnet-4-6", 100, 50)
    record("openrouter", "google/gemini-2.5-flash", 200, 100)
    summary = model_summary(days=1)
    models = [s["model"] for s in summary]
    assert "claude-sonnet-4-6" in models
    assert "google/gemini-2.5-flash" in models


def test_unknown_model_costs_zero():
    from llm_core.cost_tracker import record, total_cost
    record("custom", "my-local-model-xyz", 9999, 9999)
    # Unbekanntes Modell → $0, Gesamtkosten sollten 0 sein (frische DB)
    cost = total_cost(days=1)
    assert cost == 0.0


def test_known_model():
    from llm_core.cost_tracker import known_model
    assert known_model("claude-sonnet-4-6")
    assert known_model("claude-sonnet-4-7")  # Prefix-Match
    assert not known_model("my-custom-unknown-model")
