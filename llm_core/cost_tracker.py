"""
Cost Tracking für LLM-Aufrufe — SQLite, automatisch nach jedem Call.

DB: ~/.llm_core/costs.sqlite (oder LLM_CORE_DATA_DIR/costs.sqlite)
Preise pro 1M Tokens in USD, Stand 2025 — ohne Gewähr.
Anthropic/OpenRouter: USD. Mistral (direkt): EUR (≈ USD, <10% Abweichung).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Preistabelle: (input_per_1M_usd, output_per_1M_usd)
# ---------------------------------------------------------------------------
_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic — direkt
    "claude-opus-4-8":                        (15.0,   75.0),
    "claude-opus-4-7":                        (15.0,   75.0),
    "claude-opus-4-5":                        (15.0,   75.0),
    "claude-sonnet-4-7":                      (3.0,    15.0),
    "claude-sonnet-4-6":                      (3.0,    15.0),
    "claude-sonnet-4-5":                      (3.0,    15.0),
    "claude-haiku-4-5":                       (0.80,   4.0),
    "claude-haiku-4-5-20251001":              (0.80,   4.0),
    "claude-3-5-sonnet-20241022":             (3.0,    15.0),
    "claude-3-5-haiku-20241022":              (0.80,   4.0),
    # OpenRouter — Anthropic
    "anthropic/claude-opus-4-8":              (15.0,   75.0),
    "anthropic/claude-sonnet-4-6":            (3.0,    15.0),
    "anthropic/claude-haiku-4-5":             (0.80,   4.0),
    # OpenRouter — Google
    "google/gemini-2.5-flash":                (0.15,   0.60),
    "google/gemini-2.5-flash-preview":        (0.15,   0.60),
    "google/gemini-2.0-flash":                (0.10,   0.40),
    "google/gemini-2.0-flash-lite":           (0.075,  0.30),
    "google/gemini-2.5-pro":                  (1.25,   10.0),
    "google/gemini-3.7-flash":                (0.375,  1.875),
    # OpenRouter — Meta
    "meta-llama/llama-3.3-70b-instruct":      (0.12,   0.30),
    "meta-llama/llama-3.1-8b-instruct":       (0.055,  0.055),
    # OpenRouter — Mistral
    "mistralai/mistral-small":                (0.10,   0.30),
    # Mistral direkt (EUR ≈ USD)
    "mistral-small-latest":                   (0.10,   0.30),
    "mistral-small-4":                        (0.10,   0.30),
    "mistral-medium-latest":                  (0.40,   2.0),
    "mistral-large-latest":                   (2.0,    6.0),
    # OpenAI direkt
    "gpt-4o":                                 (2.50,   10.0),
    "gpt-4o-mini":                            (0.15,   0.60),
    "o3-mini":                                (1.10,   4.40),
}


def _cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    key = model.lower()
    pricing = _PRICING.get(key)
    if not pricing:
        for k, v in _PRICING.items():
            if key.startswith(k) or k.startswith(key):
                pricing = v
                break
    if not pricing:
        return 0.0
    in_price, out_price = pricing
    return (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000


def _db_path() -> Path:
    data_dir = os.environ.get("LLM_CORE_DATA_DIR", "~/.llm_core")
    return Path(data_dir).expanduser() / "costs.sqlite"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT NOT NULL,
            provider          TEXT NOT NULL,
            model             TEXT NOT NULL,
            prompt_tokens     INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd          REAL NOT NULL DEFAULT 0.0
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------

def record(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Speichert einen LLM-Aufruf mit berechneten Kosten. Niemals blockierend."""
    cost = _cost_usd(model, prompt_tokens, completion_tokens)
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = _connect()
        conn.execute(
            "INSERT INTO llm_usage "
            "(timestamp, provider, model, prompt_tokens, completion_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, provider, model, prompt_tokens, completion_tokens, cost),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def daily_summary(days: int = 30) -> list[dict]:
    """Tages-Aggregation: [{date, cost_usd, prompt_tokens, completion_tokens}]"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT substr(timestamp, 1, 10) as day, "
            "SUM(cost_usd), SUM(prompt_tokens), SUM(completion_tokens) "
            "FROM llm_usage WHERE timestamp >= ? "
            "GROUP BY day ORDER BY day",
            (since,),
        ).fetchall()
        conn.close()
        return [
            {"date": r[0], "cost_usd": r[1], "prompt_tokens": r[2], "completion_tokens": r[3]}
            for r in rows
        ]
    except Exception:
        return []


def model_summary(days: int = 30) -> list[dict]:
    """Kosten pro Modell: [{model, provider, cost_usd, calls, prompt_tokens, completion_tokens}]"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT model, provider, SUM(cost_usd), COUNT(*), "
            "SUM(prompt_tokens), SUM(completion_tokens) "
            "FROM llm_usage WHERE timestamp >= ? "
            "GROUP BY model, provider ORDER BY SUM(cost_usd) DESC",
            (since,),
        ).fetchall()
        conn.close()
        return [
            {"model": r[0], "provider": r[1], "cost_usd": r[2],
             "calls": r[3], "prompt_tokens": r[4], "completion_tokens": r[5]}
            for r in rows
        ]
    except Exception:
        return []


def total_cost(days: int = 30) -> float:
    """Gesamtkosten der letzten N Tage in USD."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE timestamp >= ?", (since,)
        ).fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def pricing_table() -> dict[str, tuple[float, float]]:
    return dict(_PRICING)


def known_model(model: str) -> bool:
    key = model.lower()
    if key in _PRICING:
        return True
    return any(key.startswith(k) or k.startswith(key) for k in _PRICING)
