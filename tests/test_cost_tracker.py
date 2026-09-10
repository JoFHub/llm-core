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


def test_cache_tokens_billed_at_write_and_read_multiplier():
    """Cache-Schreiben (1,25x) und -Lesen (0,1x) muessen den Input-Preis
    korrekt verrechnen, statt beim Umstieg auf Prompt-Caching stillschweigend
    unter den Tisch zu fallen (waere sonst eine scheinbare Kostensenkung, die
    tatsaechlich nur fehlende Token in der Kostenauswertung sind)."""
    from llm_core.cost_tracker import record, model_summary, _cost_usd

    record(
        "anthropic", "claude-sonnet-4-6",
        prompt_tokens=1000, completion_tokens=0,
        cache_creation_tokens=1000, cache_read_tokens=1000,
    )
    summary = model_summary(days=1)
    row = next(s for s in summary if s["model"] == "claude-sonnet-4-6")
    assert row["cache_creation_tokens"] == 1000
    assert row["cache_read_tokens"] == 1000

    expected = _cost_usd("claude-sonnet-4-6", 1000, 0, 1000, 1000)
    assert row["cost_usd"] == pytest.approx(expected)
    # 1000 normale Input-Tokens (3.0 $/MTok) + 1000 Cache-Write (3.75 $/MTok)
    # + 1000 Cache-Read (0.3 $/MTok) = (3.0 + 3.75 + 0.3) / 1000 $
    assert expected == pytest.approx((3.0 + 3.75 + 0.3) / 1000)


def test_daily_summary_includes_cache_tokens():
    from llm_core.cost_tracker import record, daily_summary
    record(
        "anthropic", "claude-sonnet-4-6", 100, 50,
        cache_creation_tokens=200, cache_read_tokens=300,
    )
    summary = daily_summary(days=1)
    assert summary[0]["cache_creation_tokens"] == 200
    assert summary[0]["cache_read_tokens"] == 300


def test_migration_adds_cache_columns_to_existing_db(tmp_path, monkeypatch):
    """Bestehende costs.sqlite (vor diesem Feature) hat die neuen Spalten
    noch nicht -- _connect() muss sie nachruesten statt abzustuerzen."""
    import sqlite3
    monkeypatch.setenv("LLM_CORE_DATA_DIR", str(tmp_path))

    old_db = tmp_path / "costs.sqlite"
    conn = sqlite3.connect(str(old_db))
    conn.execute("""
        CREATE TABLE llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0
        )
    """)
    conn.commit()
    conn.close()

    from llm_core.cost_tracker import record, total_cost
    record("anthropic", "claude-sonnet-4-6", 1000, 500)
    assert total_cost(days=1) > 0
