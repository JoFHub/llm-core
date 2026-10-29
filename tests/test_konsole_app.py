"""AppTest der Konsole (Stil wie Memoria) — ohne Netz und ohne echte Keys.

Zwei getrennte Seiten: „OpenRouter“ (Memoria, Probatio) und „Anthropic“ (Fabula)."""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from llm_core import anbieter as an  # noqa: E402
from llm_core.konsole import _gemeinsam as g  # noqa: E402
from llm_core.konsole import daten  # noqa: E402
from llm_core.ui import MONATSNAMEN  # noqa: E402

APP = str(Path(__file__).parent.parent / "llm_core" / "konsole" / "app.py")


@pytest.fixture
def at(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("OPENROUTER_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    monkeypatch.setattr(an, "_aus_keyring", lambda name: None)
    monkeypatch.setattr(daten, "db_kandidaten", lambda: [])
    monkeypatch.setattr(g, "_kurs_abfragen", lambda: 0.9)   # kein Netz für den €-Kurs
    g.eur_kurs.clear()
    an.cache_leeren()
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    assert not app.exception
    return app


@pytest.fixture
def anthropic(at):
    at.switch_page("seiten/anthropic.py").run()
    assert not at.exception
    return at


def _popover_labels(app):
    return [p.proto.popover.label for p in app.get("popover")]


def _hilfe(app):
    return " ".join(m.value for m in app.get("popover")[0].markdown)


def _sidebar(app):
    return " ".join(m.value for m in app.sidebar.markdown)


def test_openrouter_ist_startseite(at):
    assert [t.value for t in at.title] == ["OpenRouter"]
    assert _popover_labels(at) == ["Hilfe", "Optionen"]
    assert at.button(key="aktualisieren").proto.label == "Aktualisieren"
    assert [t.label for t in at.tabs] == ["Übersicht", "Verwalten", "Details lokal"]
    links = [(b.proto.label, b.proto.url) for b in at.get("link_button")]
    assert links == [("Dashboard", "https://openrouter.ai/activity")]
    assert "Memoria, Probatio" in _hilfe(at)


def test_seitenleiste_menue_je_anbieter_mit_zahlen(at):
    assert [p.proto.label for p in at.sidebar.get("page_link")] == ["OpenRouter", "Anthropic"]
    sidebar = _sidebar(at)
    assert sidebar.count('class="lc-kurz"') == 2 and "lc-eur" in sidebar
    assert ">heute<" in sidebar and ">Monat<" in sidebar
    # ohne Namen der Apps, ohne „berechnet“, ohne Hinweis auf fehlende Anthropic-Keys
    assert not any(w in sidebar for w in ("Memoria", "Fabula", "berechnet", "kein Key"))
    assert "≈ 0,90 € (EZB)" in " ".join(c.value for c in at.sidebar.caption)


def test_anthropic_seite(anthropic):
    assert [t.value for t in anthropic.title] == ["Anthropic"]
    assert [t.label for t in anthropic.tabs] == ["Übersicht", "Details lokal"]
    links = [(b.proto.label, b.proto.url) for b in anthropic.get("link_button")]
    assert links == [("Dashboard", "https://platform.claude.com/usage")]
    assert "Fabula" in _hilfe(anthropic) and "Ampel" not in _hilfe(anthropic)
    # ohne API-Key weder Hinweis noch Tabelle (nur „Keine lokale Kosten-DB“ in Details lokal)
    assert [i.value[:21] for i in anthropic.info] == ["Keine lokale Kosten-D"]
    assert not anthropic.get("dataframe")
    monat = MONATSNAMEN[datetime.now(ZoneInfo("Europe/Berlin")).month - 1]
    assert [m.label for m in anthropic.metric] == ["heute · berechnet", f"{monat} · berechnet"]
    assert [m.value for m in anthropic.metric] == ["–", "–"]   # ohne Kosten-DB keine Zahl
    assert not anthropic.error


def test_anthropic_optionen_nur_in_details(anthropic):
    assert anthropic.get("popover")[1].proto.popover.disabled
    anthropic.session_state["an_reiter"] = "Details lokal"
    anthropic.run()
    assert not anthropic.get("popover")[1].proto.popover.disabled
    assert "Kosten-DB" in _hilfe(anthropic) and "Anthropic" in _hilfe(anthropic)


def test_optionen_beschriftung_zeigt_abweichungen(at):
    assert at.number_input(key="or_opt_tage").value == 7      # Standard: eine Woche
    assert _popover_labels(at)[1] == "Optionen"
    at.number_input(key="or_opt_tage").set_value(30).run()
    at.text_input(key="or_opt_zuordnung").set_value("probatio").run()
    assert _popover_labels(at)[1] == "Optionen  ·  30 Tage  ·  probatio"


def test_optionen_je_seite_getrennt_und_bleiben_beim_wechsel(at):
    at.number_input(key="or_opt_tage").set_value(30).run()
    at.switch_page("seiten/anthropic.py").run()
    assert at.number_input(key="an_opt_tage").value == 7
    at.switch_page("seiten/openrouter.py").run()
    assert at.number_input(key="or_opt_tage").value == 30


def test_hilfe_richtet_sich_nach_reiter(at):
    assert "Ampel" in _hilfe(at)
    at.session_state["or_reiter"] = "Details lokal"
    at.run()
    assert "Kosten-DB" in _hilfe(at) and "Ampel" not in _hilfe(at)
    at.session_state["or_reiter"] = "Verwalten"
    at.run()
    assert "Bestätigen" in _hilfe(at)


def test_ohne_keys_hinweise_statt_fehler(at):
    infos = [i.value for i in at.info]
    assert any("Provisioning-Key" in t for t in infos)
    assert any("Keine lokale Kosten-DB" in t for t in infos)
    assert not at.error
