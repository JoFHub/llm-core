"""Seite „Anthropic“ der Konsole: Apps direkt bei Anthropic (Fabula).

Anthropic liefert je API-Key weder Verbrauch noch Limit: die Seite zeigt die berechneten Kosten aus den
Kosten-DBs dieses Rechners, mit Admin-Key die Kosten der Organisation, und die API-Keys nur gekürzt. Vollständig
ist das nur auf dem Server, wo Fabula läuft (server/ in diesem Repo).
"""
from __future__ import annotations

import streamlit as st

from llm_core import anbieter as an
from llm_core.konsole import _gemeinsam as g
from llm_core.konsole._widgets import button_bar, help_popover, options_popover
from llm_core.ui import MONATSNAMEN, betrag

DASHBOARD = "https://platform.claude.com/usage"

REITER = ["Übersicht", "Details lokal"]
HILFE = {
    "Übersicht": f"""\
Apps direkt bei Anthropic ({g.APPS['anthropic']}). Anthropic liefert je API-Key weder Verbrauch noch Limit.

- **berechnet:** Kosten heute und im laufenden Monat aus den Kosten-DBs dieses Rechners (Token-Zahlen von
  Anthropic mal Preisliste). Fabula läuft nur auf dem Server: vollständig ist das nur in der Konsole dort.
- **Organisation:** mit `ANTHROPIC_ADMIN_KEY` die Kosten der ganzen Organisation laut Admin-API.
- **API-Keys:** aus der Umgebung (`ANTHROPIC_API_KEY*`), nur gekürzt.
- **Ausgabengrenze** und Guthaben nur in der Anthropic Console („Dashboard“ oben).
""",
    "Details lokal": g.HILFE_DETAILS.format(
        anbieter="Anthropic", schaetzung="Berechnet aus Token-Zahlen und Preisliste"),
}

# -- Titel und Knopfleiste ------------------------------------------------------

st.title("Anthropic")

with button_bar("anthropic"):
    reiter = st.session_state.get("an_reiter", REITER[0])
    with help_popover("anthropic", HILFE.get(reiter, HILFE[REITER[0]])):
        st.caption(g.HILFE_KEYS)
    if st.button("Aktualisieren", icon=":material/refresh:", key="aktualisieren"):
        an.cache_leeren()
        st.toast("Neu bei Anthropic abgefragt", icon=":material/refresh:")
    # Optionen wirken nur auf „Details lokal“, in der Übersicht ausgegraut (Muster wie Memoria → Konto)
    with options_popover(*g.optionen_details_aktiv("an"), disabled=reiter != "Details lokal"):
        g.optionen_details("an")
    st.link_button("Dashboard", DASHBOARD, icon=":material/open_in_new:", help=DASHBOARD)

keys = an.Anthropic().keys()
organisation = next((s for s in keys if s.name == an.Anthropic.ORGANISATION), None)
# Ohne API-Key („kein Key“) kein Hinweis: auf dem Server bekommt die Konsole Fabulas Key bewusst nicht
api_keys = [s for s in keys if s is not organisation and not s.fehler]

uebersicht, lokal = st.tabs(REITER, key="an_reiter", on_change="rerun")

# -- Übersicht ------------------------------------------------------------------

with uebersicht:
    heute, monat, monat_nr, fehler = g.heute_monat("anthropic")
    monatsname = MONATSNAMEN[monat_nr - 1]
    if fehler:
        st.warning(fehler, icon=":material/warning:")
    hilfe_berechnet = "Aus den Kosten-DBs dieses Rechners: Token-Zahlen von Anthropic mal Preisliste."
    spalten = st.columns(4)
    spalten[0].metric("heute · berechnet", betrag(heute), help=hilfe_berechnet)
    spalten[1].metric(f"{monatsname} · berechnet", betrag(monat), help=hilfe_berechnet)
    if organisation is not None and not organisation.fehler:
        hilfe_org = "Kosten der ganzen Organisation laut Admin-API (alle Keys, alle Rechner)."
        spalten[2].metric("heute · Organisation", betrag(organisation.heute), help=hilfe_org)
        spalten[3].metric(f"{monatsname} · Organisation", betrag(organisation.monat), help=hilfe_org)
    elif organisation is not None:
        st.warning(g.md(f"Organisation (Admin-API): {organisation.fehler}"), icon=":material/warning:")

    if api_keys:
        st.dataframe([{"Name": s.name, "Key": s.kennung} for s in api_keys], width="stretch", hide_index=True)

# -- Details lokal --------------------------------------------------------------

with lokal:
    g.details_lokal("an", "anthropic")
