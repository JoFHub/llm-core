"""Tests für den Seitenleisten-Baustein und die Konsole — ohne Netz, ohne Streamlit."""
import subprocess
import sqlite3
import sys

import pytest

from llm_core import anbieter as an
from llm_core import cost_tracker
from llm_core.konsole import daten
from llm_core.konsole.__main__ import befehl

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
    code = ("import sys, llm_core, llm_core.ui, llm_core.anbieter, llm_core.konsole, llm_core.konsole.daten;"
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


def test_kurzstatus_zeilen():
    from llm_core.ui import AMPEL_ZEICHEN, kurzstatus_zeilen
    konto = an.KontoStatus("openrouter", guthaben=30.0, verbraucht=13.7)
    keys = [
        an.KeyStatus("openrouter", "ta", kennung="sk-or-v1-…1111", limit=10.0, rest=9.9, heute=0.1, monat=0.1),
        an.KeyStatus("openrouter", "memoria", kennung="sk-or-v1-…2222", limit=None, heute=0.5, monat=3.5),
        an.KeyStatus("anthropic", "ANTHROPIC_API_KEY", kennung="…3333", limit_abfragbar=False),
    ]
    zeilen = kurzstatus_zeilen(konto, keys)
    rot, gruen, grau = (AMPEL_ZEICHEN[a] for a in (an.ROT, an.GRUEN, an.GRAU))
    assert zeilen == [
        "Rest OpenRouter: **16,30 $**",
        f"3 Keys: {rot} 1 · {gruen} 1 · {grau} 1",
        f"{rot} ohne Limit: memoria",
        "heute 0,60 $ · Monat 3,60 $",
    ]
    assert not any("1111" in z or "2222" in z for z in zeilen)   # Keys nur mit Namen


def test_kurzstatus_ohne_konto_und_keys():
    from llm_core.ui import kurzstatus_zeilen
    assert kurzstatus_zeilen(None, []) == []
    [z] = kurzstatus_zeilen(an.KontoStatus("openrouter", fehler="Zeitüberschreitung"), [])
    assert "Zeitüberschreitung" in z


def test_kurzstatus_anthropic_zeilen():
    from llm_core.ui import kurzstatus_anthropic_zeilen
    keys = [an.KeyStatus("anthropic", "ANTHROPIC_API_KEY_FABULA", kennung="sk-ant…ab12", limit_abfragbar=False),
            an.KeyStatus("anthropic", an.Anthropic.ORGANISATION, heute=1.0, monat=12.5, limit_abfragbar=False)]
    assert kurzstatus_anthropic_zeilen(keys, 0.45, 2.74, 10) == [
        "berechnet: heute 0,45 $ · Oktober 2,74 $",
        "Organisation: heute 1,00 $ · Oktober 12,50 $",
        "1 API-Key",
    ]
    ohne = [an.KeyStatus("anthropic", "ANTHROPIC_API_KEY", limit_abfragbar=False, fehler="kein Key")]
    assert kurzstatus_anthropic_zeilen(ohne, None, None, 1) == [
        "berechnet: heute – · Januar –"]   # ohne API-Key kein Hinweis (Server)


# -- Konsole: Start und lokale Details ---------------------------------------------

def test_konsole_startbefehl_nur_localhost():
    cmd = befehl(8510, "localhost")
    assert cmd[1:4] == ["-m", "streamlit", "run"] and cmd[4].endswith("app.py")
    assert cmd[cmd.index("--server.address") + 1] == "localhost"
    assert cmd[cmd.index("--server.port") + 1] == "8510"


def test_details_lokal(tmp_path):
    cost_tracker.record("openrouter", "google/gemini-2.5-flash", 100, 50, cost_usd_override=0.5, tag="visibilia")
    cost_tracker.record("anthropic", "claude-sonnet-4-6", 1000, 100, tag="")
    db = tmp_path / "costs.sqlite"
    d = daten.details(db, tage=30)
    assert d["fehler"] is None and len(d["je_tag"]) == 1 and len(d["je_modell"]) == 2
    assert {z["zuordnung"] for z in d["je_tag_zuordnung"]} == {"visibilia", "(ohne)"}
    nur = daten.details(db, tage=30, tag="visibilia")
    assert nur["summe"] == pytest.approx(0.5)
    nur_or = daten.details(db, tage=30, provider="openrouter")
    assert [m["modell"] for m in nur_or["je_modell"]] == ["google/gemini-2.5-flash"]
    assert nur_or["summe"] == pytest.approx(0.5)


def test_kosten_seit_mehrere_dbs_je_anbieter(tmp_path):
    from datetime import datetime, timezone
    dbs = []
    for name, kosten in (("a", 1.0), ("b", 2.0)):
        db = tmp_path / name / "costs.sqlite"
        db.parent.mkdir()
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE llm_usage (timestamp TEXT, provider TEXT, cost_usd REAL)")
        conn.executemany("INSERT INTO llm_usage VALUES (?, ?, ?)", [
            ("2026-10-29T06:00:00+00:00", "anthropic", kosten),
            ("2026-10-29T06:00:00+00:00", "openrouter", 10.0),
            ("2026-09-30T06:00:00+00:00", "anthropic", 100.0)])
        conn.commit()
        conn.close()
        dbs.append(db)
    seit = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert daten.kosten_seit(dbs, seit, "anthropic") == (pytest.approx(3.0), None)
    summe, fehler = daten.kosten_seit(dbs + [tmp_path / "fehlt" / "costs.sqlite"], seit, "anthropic")
    assert summe == pytest.approx(3.0) and "DB nicht lesbar" in fehler


def test_details_alt_db_ohne_tag_spalte(tmp_path):
    db = tmp_path / "alt" / "costs.sqlite"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE llm_usage (id INTEGER PRIMARY KEY, timestamp TEXT, provider TEXT, model TEXT, "
                 "prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL)")
    conn.execute("INSERT INTO llm_usage VALUES (1, '2999-01-01T00:00:00', 'p', 'm', 1, 1, 2.0)")
    conn.commit()
    conn.close()
    d = daten.details(db, tage=30)
    assert d["fehler"] is None and d["summe"] == pytest.approx(2.0)
    assert daten.details(db, tage=30, tag="x")["summe"] == 0.0


def test_details_fehlende_db_wird_nicht_angelegt(tmp_path):
    db = tmp_path / "gibtsnicht" / "costs.sqlite"
    assert daten.details(db)["fehler"]
    assert not db.exists()


def test_db_kandidaten(tmp_path, monkeypatch):
    memoria = tmp_path / "memoria"
    memoria.mkdir()
    (memoria / "costs.sqlite").write_bytes(b"")
    monkeypatch.setattr(daten, "STANDARD_ORDNER", ())
    monkeypatch.setenv("LLM_CORE_KONSOLE_DBS", f"{memoria}{__import__('os').pathsep}{tmp_path / 'leer'}")
    cost_tracker.record("p", "m", 0, 0)   # legt costs.sqlite in LLM_CORE_DATA_DIR (= tmp_path) an
    assert daten.db_kandidaten() == [(memoria / "costs.sqlite").resolve(), (tmp_path / "costs.sqlite").resolve()]


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

