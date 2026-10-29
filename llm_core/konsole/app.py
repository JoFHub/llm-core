"""Einstieg der Kosten-Konsole. Start über `python -m llm_core.konsole` (nur localhost).

Zwei getrennte Seiten, je Anbieter eine (Trennung seit 2026-10-29): „OpenRouter“ (Memoria, Probatio) und
„Anthropic“ (Fabula), in `seiten/`. Die Seitenleiste zeigt unter der Navigation den Kurzstatus beider.

Stil und Bedienung wie Memoria (GUI-Standards in memoria/CLAUDE.md): Theme in `.streamlit/config.toml`
neben dieser Datei, globales CSS und Knopfleiste in `_widgets.py`.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from llm_core import anbieter as an  # lädt über llm_core.config auch die .env
from llm_core.konsole import _gemeinsam as g
from llm_core.konsole._widgets import CSS
from llm_core.ui import kurzstatus_anthropic_zeilen, kurzstatus_zeilen

st.set_page_config(page_title="LLM-Kosten", page_icon=":material/payments:", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(CSS, unsafe_allow_html=True)

# Optionen je Seite; neu zuweisen, sonst verwirft Streamlit sie beim Seitenwechsel mit dem Widget
for name, wert in g.OPTIONEN.items():
    st.session_state.setdefault(name, wert)
    st.session_state[name] = st.session_state[name]

pg = st.navigation([
    st.Page("seiten/openrouter.py", title="OpenRouter", icon=":material/hub:", default=True),
    st.Page("seiten/anthropic.py", title="Anthropic", icon=":material/auto_awesome:"),
])
pg.run()

# -- Seitenleiste: Kurzstatus beider Anbieter, nach der Seite (zeigt dann „Aktualisieren“ schon) ---------------

with st.sidebar:
    openrouter = an.OpenRouter()
    zeilen = kurzstatus_zeilen(openrouter.konto(), openrouter.keys(), g.schwellen())
    st.markdown(g.md(f"**OpenRouter** · {g.APPS['openrouter']}  \n" + "  \n".join(zeilen)))
    heute, monat, monat_nr, _ = g.heute_monat("anthropic")
    zeilen = kurzstatus_anthropic_zeilen(an.Anthropic().keys(), heute, monat, monat_nr)
    st.markdown(g.md(f"**Anthropic** · {g.APPS['anthropic']}  \n" + "  \n".join(zeilen)))
    st.caption(f"Stand {datetime.now():%H:%M} · zwischengespeichert bis 60 s")
