"""Gemeinsames der Konsolen-Seiten (OpenRouter, Anthropic): Optionen, Formatierung, Details lokal.

Jede Seite zeigt nur ihren Anbieter. Die Optionen haben je Seite eigene Schlüssel (`or_…`, `an_…`);
app.py legt sie an und hält sie beim Seitenwechsel fest.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from llm_core import anbieter as an
from llm_core.konsole import daten
from llm_core.ui import betrag

ZEITZONE = ZoneInfo("Europe/Berlin")

# Welche App über welchen Anbieter läuft (Trennung seit 2026-10-29)
APPS = {"openrouter": "Memoria, Probatio", "anthropic": "Fabula"}

STANDARD = an.Schwellen.from_env()
STANDARD_TAGE = 7                   # Details lokal: eine Woche (Nutzer, 2026-10-26)

# Startwerte der Optionen je Seite
OPTIONEN = {
    "or_opt_gelb": STANDARD.gelb,
    "or_opt_tage": STANDARD_TAGE,
    "or_opt_zuordnung": "",
    "or_opt_db_extra": "",
    "an_opt_tage": STANDARD_TAGE,
    "an_opt_zuordnung": "",
    "an_opt_db_extra": "",
}

# primaryColor je Variante, wie in .streamlit/config.toml
THEME_FARBE = {"light": "#3d5166", "dark": "#8aa4bf"}

# Umrechnung in € (ungefähr): Referenzkurs der EZB über frankfurter.dev (ohne Key), einmal am Tag;
# LLM_CORE_EUR_KURS setzt einen festen Kurs, ohne Kurs entfällt die €-Spalte
EUR_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR"

HILFE_KEYS = ("Keys kommen aus der `.env` im Ordner von llm-core oder aus der "
              "Windows-Anmeldeinformationsverwaltung (Dienst `llm-core`).")

HILFE_DETAILS = """\
Kosten je Tag, Modell und Zuordnung (`tag`) aus einer lokalen Kosten-DB, nur lesend und nur {anbieter}.

- {schaetzung}; nur, was in der gewählten DB protokolliert wurde.
- Zeitraum, Zuordnung und weitere DB unter „Optionen“ (Standard: 7 Tage).
"""


def md(text: str) -> str:
    return text.replace("$", "\\$")   # "$" sonst Formel


def theme_farbe() -> str:
    """primaryColor aus .streamlit/config.toml, passend zu hell/dunkel (Diagramme übernehmen sie nicht)."""
    try:
        dunkel = st.context.theme.type == "dark"
    except Exception:
        dunkel = False
    return THEME_FARBE["dark" if dunkel else "light"]


def _kurs_abfragen() -> float | None:
    try:
        with urllib.request.urlopen(EUR_URL, timeout=5) as antwort:
            return float(json.load(antwort)["rates"]["EUR"])
    except Exception:
        return None


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def eur_kurs() -> float | None:
    """Euro je Dollar, ungefähr; None, wenn kein Kurs zu bekommen ist."""
    fest = os.environ.get("LLM_CORE_EUR_KURS", "").strip().replace(",", ".")
    if fest:
        try:
            return float(fest)
        except ValueError:
            pass
    return _kurs_abfragen()


def kurz_html(werte: list[tuple[str, float | None]], kurs: float | None) -> str:
    """Werte der Seitenleiste untereinander: Name, Betrag in $, ungefähr in € (Raster, Klassen in _widgets.CSS)."""
    zellen = []
    for name, usd in werte:
        eur = f"≈ {betrag(usd * kurs, '€')}" if kurs and usd is not None else ""
        # "$" als Entität: Streamlit liest "$…$" sonst als Formel
        zellen.append(f'<span>{escape(name)}</span><span class="lc-usd">{betrag(usd).replace("$", "&#36;")}</span>'
                      f'<span class="lc-eur">{eur}</span>')
    return f'<div class="lc-kurz">{"".join(zellen)}</div>'


def schwellen() -> an.Schwellen:
    gelb = st.session_state.or_opt_gelb
    return an.Schwellen(gelb=gelb, rot=max(STANDARD.rot, gelb))


def tage_text(tage: int) -> str:
    return f"{tage} {'Tag' if tage == 1 else 'Tage'}"


def optionen_details(prefix: str) -> list[str]:
    """Felder für „Details lokal“ im Fenster „Optionen“; gibt die Abweichungen vom Standard zurück."""
    st.number_input("Tage", min_value=1, max_value=3650, step=1, key=f"{prefix}_opt_tage")
    st.text_input("Zuordnung (tag), leer = alle", key=f"{prefix}_opt_zuordnung")
    st.text_input("Weitere DB (Ordner oder costs.sqlite)", key=f"{prefix}_opt_db_extra")
    return optionen_details_aktiv(prefix)


def optionen_details_aktiv(prefix: str) -> list[str]:
    tage = st.session_state[f"{prefix}_opt_tage"]
    return [tage_text(tage) if tage != STANDARD_TAGE else "",
            st.session_state[f"{prefix}_opt_zuordnung"].strip()]


def kosten_dbs(prefix: str | None = None) -> tuple[list[Path], Path | None]:
    """Gefundene Kosten-DBs, mit der weiteren DB aus „Optionen“ vorne; dazu der Pfad, falls diese fehlt."""
    kandidaten = daten.db_kandidaten()
    eigene = st.session_state[f"{prefix}_opt_db_extra"].strip() if prefix else ""
    if not eigene:
        return kandidaten, None
    pfad = Path(eigene).expanduser()
    pfad = pfad if pfad.suffix == ".sqlite" else pfad / daten.DB_NAME
    if not pfad.is_file():
        return kandidaten, pfad
    if pfad.resolve() not in kandidaten:
        kandidaten.insert(0, pfad.resolve())
    return kandidaten, None


def heute_monat(provider: str) -> tuple[float | None, float | None, int, str | None]:
    """Kosten heute und im laufenden Monat (Europe/Berlin) aus allen Kosten-DBs dieses Rechners.
    (heute, monat, Monatsnummer, Fehler); ohne DB (None, None, …)."""
    jetzt = datetime.now(ZEITZONE)
    tag_beginn = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
    dbs, _ = kosten_dbs()
    if not dbs:
        return None, None, jetzt.month, None
    heute, f1 = daten.kosten_seit(dbs, tag_beginn, provider)
    monat, f2 = daten.kosten_seit(dbs, tag_beginn.replace(day=1), provider)
    return heute, monat, jetzt.month, f1 or f2


def details_lokal(prefix: str, provider: str) -> None:
    """Reiter „Details lokal“: Kosten aus einer gewählten Kosten-DB, nur dieser Anbieter."""
    kandidaten, fehlt = kosten_dbs(prefix)
    if fehlt:
        st.warning(f"Keine Kosten-DB unter {fehlt}", icon=":material/warning:")
    if not kandidaten:
        st.info("Keine lokale Kosten-DB gefunden (`~/.llm_core`, `~/.memoria`, `LLM_CORE_DATA_DIR`).",
                icon=":material/info:")
        return
    tage = int(st.session_state[f"{prefix}_opt_tage"])
    zuordnung = st.session_state[f"{prefix}_opt_zuordnung"].strip()
    db = st.selectbox("Datenbank", kandidaten, format_func=str, key=f"{prefix}_db")
    d = daten.details(db, tage, zuordnung or None, provider=provider)
    if d["fehler"]:
        st.error(d["fehler"], icon=":material/error:")
        return
    st.metric(f"Summe {tage_text(tage)} (Schätzung)", betrag(d["summe"]))
    if d["je_tag"]:
        import pandas as pd
        st.bar_chart(pd.DataFrame(d["je_tag"]).set_index("datum")["kosten_usd"], color=theme_farbe())
    # Spalte „anbieter“ entfällt: die Seite zeigt nur einen
    st.markdown("**Je Modell**")
    st.dataframe(d["je_modell"], width="stretch", hide_index=True,
                 column_order=["modell", "kosten_usd", "aufrufe", "prompt_tokens", "completion_tokens"])
    st.markdown("**Je Zuordnung**")
    st.dataframe(d["je_tag_zuordnung"], width="stretch", hide_index=True,
                 column_order=["zuordnung", "kosten_usd", "aufrufe", "letzter"])
