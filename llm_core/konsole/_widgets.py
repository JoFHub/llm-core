"""Bausteine der Konsole im Stil von Memoria (memoria/gui/_widgets.py, GUI-Standards in memoria/CLAUDE.md).

Knopfleiste unter dem Titel: zuerst „Hilfe“, dann Aktionen, dann „Optionen“, zuletzt Seiteneigenes.
Das globale CSS (``CSS``) bindet app.py ein.
"""
from __future__ import annotations

from contextlib import contextmanager

import streamlit as st

# Globales CSS — Auszug aus memoria/gui/app.py: gedämpfte Farben, kompakte Abstände.
CSS = """
<style>
/* Platz der Bildlaufleiste immer freihalten, sonst springt der Inhalt */
html, :is(section, main, aside, nav, div)[data-testid="stMain"] { scrollbar-gutter: stable; }
/* Farben zentral: Inline-HTML nutzt var(--lc-…) statt fester Werte */
:root {
    --lc-muted: #888;
    --lc-success: #2e7d32;
    --lc-warning: #ef6c00;
    --lc-error: #c62828;
}
div[data-testid="stMetric"] { padding-bottom: 0 !important; }
[data-testid="stMain"] hr { margin: 0.2rem 0 !important; }
.stMainBlockContainer > div > [data-testid="stVerticalBlock"] { gap: 0.5rem !important; }
/* Titel: wenig Abstand nach oben, erste Zeile knapp unter dem Header */
h1 { margin-top: 0 !important; margin-bottom: 0.5rem !important; }
[data-testid="stMainBlockContainer"] { padding-top: 4rem !important; }
[data-testid="stHeading"] h1 { padding: 0.625rem 0 1rem 0 !important; }
/* CSS-only-Markdown-Blöcke (<style>) aus dem Layout nehmen */
[data-testid="stElementContainer"]:has(> .stMarkdown [data-testid="stMarkdownContainer"] > style:only-child) { display: none !important; }
/* Fenster „Hilfe“: Erklärtext kleiner als die Inhalte der Seite */
[class*="st-key-pop_help_"] [data-testid="stMarkdownContainer"] :is(p, li) { font-size: 0.875rem !important; }
[data-testid="stCaptionContainer"] p { line-height: 1.28 !important; margin-bottom: 0 !important; }
summary p { font-size: 0.91rem !important; }
[data-testid="stExpander"] [data-testid="stVerticalBlock"] { gap: 0.85rem !important; }
/* Panels (st.container(border=True, key="panel_<name>")): kompakte Innenabstände, gleiche Höhe */
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] {
    flex: 1 !important;
    padding: 0.55rem 0.75rem !important;
    gap: 0.15rem !important;
}
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] hr { display: none !important; }
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] p { margin-bottom: 0 !important; line-height: 1.3 !important; }
/* Markdown/Caption haben margin-bottom -1rem (gleicht Streamlits Gap aus) — beim kompakten Panel-Gap
   rutscht die nächste Zeile sonst in die Linie der Kopfzeile */
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] [data-testid="stCaptionContainer"],
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] [data-testid="stMarkdown"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
/* Erste Zeile: Abstand zur Kopfzeile */
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] > div:nth-child(2) { margin-top: 0.75rem !important; }
/* Zeilen im Panel kleiner als die Kopfzeile (Memoria: 0.85rem) */
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] [data-testid="stMarkdownContainer"] :is(p, li):not(.lc-panel-head) { font-size: 0.875rem !important; }
/* Knöpfe unter den Zeilen: etwas Luft */
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] [data-testid="stHorizontalBlock"],
div[data-testid="stVerticalBlock"][class*="st-key-panel_"] > div:has(.stButton) { margin-top: 0.5rem !important; }
/* Kompaktere Dialoge */
div[data-testid="stDialog"] div[data-testid="stVerticalBlock"] { gap: 0.4rem !important; }
div[data-testid="stDialog"] [data-testid="stCaptionContainer"] { margin-bottom: 0 !important; }
div[data-testid="stDialog"] h2 { font-size: 1rem !important; font-weight: 500 !important; }
div[data-testid="stDialog"] { padding: 1rem !important; }
[data-testid="stMain"] .stButton button { border-width: 1px !important; }
</style>
"""


def button_bar(page: str):
    """Knopfleiste unter dem Titel (Container ``pop_list_<page>``)."""
    return st.container(horizontal=True, gap="small", key=f"pop_list_{page}")


@contextmanager
def help_popover(page: str, caption: str = ""):
    """Fenster „Hilfe“; oben die kurze Beschreibung der Seite (statt unter dem Titel)."""
    with st.popover("Hilfe", icon=":material/help:"), st.container(key=f"pop_help_{page}"):
        if caption:
            st.markdown(caption)
        yield


def options_popover(*active: str, disabled: bool = False):
    """Fenster „Optionen“ (Icon ``tune``); die Beschriftung zeigt nur Abweichungen vom Standard."""
    label = "  ·  ".join(["Optionen", *[a for a in active if a]])
    return st.popover(label, icon=":material/tune:", disabled=disabled)


def panel_header(text: str) -> None:
    """Kopfzeile eines Panels: fett, mit dünner Linie darunter."""
    st.markdown('<p class="lc-panel-head" style="font-weight:600;border-bottom:1px solid rgba(128,128,128,0.25);'
                f'padding-bottom:0.2rem;margin-bottom:0.3rem;">{text}</p>', unsafe_allow_html=True)
