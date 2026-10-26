"""AppTest der Konsolen-Seite (Stil wie Memoria) — ohne Netz und ohne echte Keys."""
import os
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from llm_core import anbieter as an  # noqa: E402
from llm_core.konsole import daten  # noqa: E402

APP = str(Path(__file__).parent.parent / "llm_core" / "konsole" / "app.py")


@pytest.fixture
def at(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("OPENROUTER_", "ANTHROPIC_ADMIN")):
            monkeypatch.delenv(name)
    monkeypatch.setattr(an, "_aus_keyring", lambda name: None)
    monkeypatch.setattr(daten, "db_kandidaten", lambda: [])
    an.cache_leeren()
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    assert not app.exception
    return app


def _popover_labels(app):
    return [p.proto.popover.label for p in app.get("popover")]


def test_knopfleiste_hilfe_aktualisieren_optionen(at):
    assert [t.value for t in at.title] == ["LLM-Kosten"]
    assert _popover_labels(at) == ["Hilfe", "Optionen"]
    assert at.button(key="aktualisieren").proto.label == "Aktualisieren"
    assert len(at.sidebar.children) == 0   # keine Seitenleiste mehr
    links = [(b.proto.label, b.proto.url) for b in at.get("link_button")]
    assert links == [("OpenRouter", "https://openrouter.ai/activity"),
                     ("Anthropic", "https://platform.claude.com/usage")]


def test_optionen_beschriftung_zeigt_abweichungen(at):
    assert at.number_input(key="opt_tage").value == 7      # Standard: eine Woche
    assert _popover_labels(at)[1] == "Optionen"
    at.number_input(key="opt_tage").set_value(30).run()
    at.text_input(key="opt_zuordnung").set_value("probatio").run()
    assert _popover_labels(at)[1] == "Optionen  ·  30 Tage  ·  probatio"


def _hilfe(app):
    return " ".join(m.value for m in app.get("popover")[0].markdown)


def test_hilfe_richtet_sich_nach_reiter(at):
    assert "Ampel" in _hilfe(at)
    at.session_state["reiter"] = "Details lokal"
    at.run()
    assert "Kosten-DB" in _hilfe(at) and "Ampel" not in _hilfe(at)
    at.session_state["reiter"] = "OpenRouter verwalten"
    at.run()
    assert "Bestätigen" in _hilfe(at)


def test_ohne_keys_hinweise_statt_fehler(at):
    infos = [i.value for i in at.info]
    assert any("Provisioning-Key" in t for t in infos)
    assert any("Keine lokale Kosten-DB" in t for t in infos)
    assert not at.error
