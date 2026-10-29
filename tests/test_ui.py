"""Tests für den Seitenleisten-Baustein — ohne Netz, ohne Streamlit."""
import subprocess
import sys

import pytest

from llm_core import anbieter as an
from llm_core import cost_tracker

KEY = "sk-or-v1-" + "0" * 60 + "a1b2"


class Fest:
    """Adapter-Ersatz mit fester Antwort."""

    def __init__(self, status: an.KeyStatus):
        self.status = status
        self.aufrufe = []

    def key_status(self, key, name=""):
        self.aufrufe.append(key)
        return self.status


@pytest.fixture(autouse=True)
def _sauber(monkeypatch):
    an.cache_leeren()
    monkeypatch.delenv("OPENROUTER_API_KEY_TEST", raising=False)


def test_llm_core_haengt_nicht_von_streamlit_ab():
    code = ("import sys, llm_core, llm_core.ui, llm_core.anbieter;"
            "sys.exit(1 if 'streamlit' in sys.modules else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_betrag():
    from llm_core.ui import betrag
    assert betrag(4.1234) == "4,12 $"
    assert betrag(1234.5) == "1.234,50 $"
    assert betrag(0.004) == "0,004 $"
    assert betrag(0) == "0,00 $"
    assert betrag(None) == "–"


def test_zeilen_mit_limit(monkeypatch):
    from llm_core.ui import kosten_zeilen
    monkeypatch.setenv("OPENROUTER_API_KEY_TEST", KEY)
    fest = Fest(an.KeyStatus("openrouter", "x", limit=10.0, rest=1.5, verbrauch_gesamt=8.5, heute=0.5, monat=6.0))
    zeilen = kosten_zeilen("OPENROUTER_API_KEY_TEST", adapter=fest)
    assert zeilen == [":orange[:material/warning:] 8,50 $ von 10,00 $ (85 %)"]
    assert fest.aufrufe == [KEY]
    assert not any("a1b2" in z or KEY in z for z in zeilen)   # nichts vom Key im HTML


def test_zeilen_ohne_limit_und_monatslimit(monkeypatch):
    from llm_core.ui import kosten_zeilen
    monkeypatch.setenv("OPENROUTER_API_KEY_TEST", KEY)
    zeilen = kosten_zeilen("OPENROUTER_API_KEY_TEST",
                           adapter=Fest(an.KeyStatus("openrouter", "x", verbrauch_gesamt=3.0, heute=0, monat=1)))
    assert zeilen[0] == ":red[:material/error:] 3,00 $ · ohne Limit"
    zeilen = kosten_zeilen("OPENROUTER_API_KEY_TEST", adapter=Fest(an.KeyStatus(
        "openrouter", "x", limit=10.0, rest=9.0, verbrauch_gesamt=50.0, limit_reset="monthly")))
    assert zeilen[0] == ":green[:material/check_circle:] 1,00 $ von 10,00 $ Monat (10 %)"


def test_zeilen_fehler_ohne_schaetzung(monkeypatch):
    from llm_core.ui import kosten_zeilen
    cost_tracker.record("openrouter", "x", 0, 0, cost_usd_override=1.25)
    monkeypatch.setenv("OPENROUTER_API_KEY_TEST", KEY)
    fest = Fest(an.KeyStatus("openrouter", "x", fehler="Key ungültig oder widerrufen (401)"))
    zeilen = kosten_zeilen("OPENROUTER_API_KEY_TEST", adapter=fest)
    assert zeilen == [":gray[:material/help:] Anbieter: Key ungültig oder widerrufen (401)"]   # Kosten-DB bleibt außen vor


def test_zeilen_ohne_key_fragt_nicht_an():
    from llm_core.ui import kosten_zeilen
    fest = Fest(an.KeyStatus("openrouter", "x"))
    zeilen = kosten_zeilen("OPENROUTER_API_KEY_TEST", adapter=fest)
    assert zeilen == [":gray[:material/help:] kein Key (OPENROUTER_API_KEY_TEST)"]
    assert fest.aufrufe == []


def test_seitenleiste_ohne_streamlit_klare_meldung(monkeypatch):
    from llm_core.ui import kosten_seitenleiste
    monkeypatch.setitem(sys.modules, "streamlit", None)
    with pytest.raises(ImportError):
        kosten_seitenleiste("OPENROUTER_API_KEY_TEST")


def test_betrag_in_euro():
    from llm_core.ui import betrag
    assert betrag(1234.5) == "1.234,50 $" and betrag(0.004, "€") == "0,004 €" and betrag(None, "€") == "–"


def test_berechnet_zeilen_heute_und_monat(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from llm_core import cost_tracker, ui

    monkeypatch.setenv("LLM_CORE_DATA_DIR", str(tmp_path))
    conn = cost_tracker._connect()
    zeilen = [  # (Zeitpunkt UTC, Provider, Kosten, Zuordnung)
        ("2026-10-29T06:00:00+00:00", "anthropic", 0.45, "fabula"),   # heute (07:00 in Berlin)
        ("2026-10-28T22:30:00+00:00", "anthropic", 0.10, "fabula"),   # 23:30 in Berlin: gestern, obwohl erst 22:30 UTC
        ("2026-10-02T10:00:00+00:00", "anthropic", 2.00, "fabula"),   # diesen Monat
        ("2026-09-30T10:00:00+00:00", "anthropic", 5.00, "fabula"),   # Vormonat
        ("2026-10-29T06:00:00+00:00", "openrouter", 1.00, "fabula"),  # anderer Anbieter
        ("2026-10-29T06:00:00+00:00", "anthropic", 3.00, "probatio"),  # andere App
    ]
    for ts, provider, kosten, tag in zeilen:
        conn.execute("INSERT INTO llm_usage (timestamp, provider, model, cost_usd, tag) VALUES (?, ?, 'm', ?, ?)",
                     (ts, provider, kosten, tag))
    conn.commit(); conn.close()

    jetzt = datetime(2026, 10, 29, 8, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert ui.berechnet_zeilen("anthropic", tag="fabula", jetzt=jetzt) == [
        "heute 0,45 $ · Oktober 2,55 $"]
    assert cost_tracker.cost_since(datetime(2026, 10, 1, tzinfo=timezone.utc)) == 6.55

