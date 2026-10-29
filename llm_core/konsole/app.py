"""Einstieg der Kosten-Konsole. Start über `python -m llm_core.konsole` (lokal nur localhost; Server: server/).

Zwei getrennte Seiten, je Anbieter eine (Trennung seit 2026-10-29): „OpenRouter“ und „Anthropic“, in `seiten/`.
Die Seitenleiste baut die Navigation selbst: je Anbieter ein Menüpunkt, darunter heute, Monat (und Rest) in $ und
ungefähr in €, Hinweise nur, wenn etwas nicht stimmt.

Stil und Bedienung wie Memoria (GUI-Standards in memoria/CLAUDE.md): Theme in `.streamlit/config.toml`
neben dieser Datei, globales CSS und Knopfleiste in `_widgets.py`.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from llm_core import anbieter as an  # lädt über llm_core.config auch die .env
from llm_core.konsole import _gemeinsam as g
from llm_core.konsole._widgets import CSS
from llm_core.ui import betrag, kurzstatus_hinweise, kurzstatus_werte

# "auto": auf dem iPhone zugeklappt (sie läge sonst über der Seite), am Rechner offen
st.set_page_config(page_title="LLM-Kosten", page_icon=":material/payments:", layout="wide",
                   initial_sidebar_state="auto")
st.markdown(CSS, unsafe_allow_html=True)

# Optionen je Seite; neu zuweisen, sonst verwirft Streamlit sie beim Seitenwechsel mit dem Widget
for name, wert in g.OPTIONEN.items():
    st.session_state.setdefault(name, wert)
    st.session_state[name] = st.session_state[name]

ZUKLAPPEN = """<script>
if (window.innerWidth < 640) {
  document.querySelector('[data-testid="stSidebar"][data-collapsed="false"] [data-testid="stSidebarCollapseButton"] button')
    ?.click();
}
</script>"""

openrouter_seite = st.Page("seiten/openrouter.py", title="OpenRouter", icon=":material/hub:", default=True)
anthropic_seite = st.Page("seiten/anthropic.py", title="Anthropic", icon=":material/auto_awesome:")
pg = st.navigation([openrouter_seite, anthropic_seite], position="hidden")
pg.run()

# iPhone: Menüpunkte (st.page_link) klappen die Seitenleiste nicht zu wie die eingebaute Navigation; nach einem
# Seitenwechsel auf schmalem Bildschirm daher den Knopf „zuklappen“ drücken (sie läge sonst über der Seite)
if st.session_state.get("_seite") not in (None, pg.url_path):
    st.html(ZUKLAPPEN, unsafe_allow_javascript=True)
st.session_state["_seite"] = pg.url_path

# -- Seitenleiste: Menüpunkt je Anbieter mit seinen Zahlen, nach der Seite (zeigt dann „Aktualisieren“ schon) -----

kurs = g.eur_kurs()
with st.sidebar:
    openrouter = an.OpenRouter()
    konto, keys = openrouter.konto(), openrouter.keys()
    with st.container(key="kurz_openrouter"):
        st.page_link(openrouter_seite, width="stretch")
        st.markdown(g.kurz_html(kurzstatus_werte(konto, keys), kurs), unsafe_allow_html=True)
        for hinweis in kurzstatus_hinweise(konto, keys, g.schwellen()):
            st.markdown(g.md(hinweis))
    heute, monat, _, fehler = g.heute_monat("anthropic")
    with st.container(key="kurz_anthropic"):
        st.page_link(anthropic_seite, width="stretch")
        st.markdown(g.kurz_html([("heute", heute), ("Monat", monat)], kurs), unsafe_allow_html=True)
        if fehler:
            st.markdown(g.md(f":gray[:material/help:] {fehler}"))
    stand = f"Stand {datetime.now(g.ZEITZONE):%H:%M}"
    st.caption(g.md(f"{stand} · 1 $ ≈ {betrag(kurs, '€')} (EZB)" if kurs else stand))
