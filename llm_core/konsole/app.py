"""Streamlit-Seite der Kosten-Konsole. Start über `python -m llm_core.konsole` (nur localhost).

Stil und Bedienung wie Memoria (GUI-Standards in memoria/CLAUDE.md): Theme in `.streamlit/config.toml`
neben dieser Datei, globales CSS und Knopfleiste in `_widgets.py`.

Keys erscheinen nur gekürzt; einzige Ausnahme ist der Klartext eines neu angelegten Keys, genau einmal.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from llm_core import anbieter as an  # lädt über llm_core.config auch die .env
from llm_core.konsole import daten
from llm_core.konsole._widgets import CSS, button_bar, help_popover, options_popover, panel_header
from llm_core.ui import betrag

st.set_page_config(page_title="LLM-Kosten", page_icon=":material/payments:", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(CSS, unsafe_allow_html=True)

RESETS = {"nie (Limit gilt insgesamt)": None, "täglich": "daily", "wöchentlich": "weekly", "monatlich": "monthly"}

# Ampel in der Tabelle: typografischer Punkt, Farbe wie --lc-… in _widgets.CSS (Tabellen sind ein Canvas,
# CSS-Variablen greifen dort nicht)
AMPEL_FARBE = {an.GRUEN: "#2e7d32", an.GELB: "#ef6c00", an.ROT: "#c62828", an.GRAU: "#888"}
AMPEL_TEXT = {an.GRUEN: "im Limit", an.GELB: "Limit fast erreicht", an.ROT: "Limit erreicht oder ohne Limit",
              an.GRAU: "unbekannt"}

# primaryColor je Variante, wie in .streamlit/config.toml
THEME_FARBE = {"light": "#3d5166", "dark": "#8aa4bf"}

DASHBOARDS = {
    "OpenRouter": "https://openrouter.ai/activity",
    "Anthropic": "https://platform.claude.com/usage",
}

STANDARD = an.Schwellen.from_env()
STANDARD_TAGE = 7                   # Details lokal: eine Woche (Nutzer, 2026-10-26)

# Text im Fenster „Hilfe“ je Reiter (Reiter mit key + on_change="rerun", Muster wie Memoria → Konto)
REITER = ["Übersicht", "OpenRouter verwalten", "Details lokal"]
HILFE = {
    "Übersicht": """\
Verbrauch, Limits und Keys direkt beim Anbieter, in USD, zwischengespeichert bis 60 s.

- **Ampel:** grün im Limit, orange ab der Schwelle unter „Optionen“, rot bei erreichtem Limit oder
  Key ohne Limit, grau bei Fehler oder nicht abfragbarem Limit.
- **OpenRouter:** alle Keys und das Guthaben mit Provisioning-Key (`OPENROUTER_PROVISIONING_KEY`), sonst
  nur die Keys aus der Umgebung (`OPENROUTER_API_KEY*`).
- **Anthropic:** nur die API-Keys (`ANTHROPIC_API_KEY*`, gekürzt) — Anthropic liefert je Key weder
  Verbrauch noch Limit; Verbrauch im Dashboard („Anthropic“ oben). Mit `ANTHROPIC_ADMIN_KEY` zusätzlich
  die Kosten der Organisation.
""",
    "OpenRouter verwalten": """\
Limit ändern, Key aktivieren oder deaktivieren, neuen Key anlegen — nur OpenRouter.

- Jede Aktion zeigt vorher/nachher und läuft erst nach „Bestätigen“.
- Der Klartext eines neuen Keys erscheint genau einmal: sofort in die `.env` der App bzw. den
  Passwortmanager übernehmen.
- Braucht den Provisioning-Key (`OPENROUTER_PROVISIONING_KEY`).
""",
    "Details lokal": """\
Kosten je Tag, Modell und Zuordnung (`tag`) aus einer lokalen Kosten-DB, nur lesend.

- Schätzungen aus der Preistabelle (OpenRouter: echter Preis je Aufruf); nur, was auf diesem Rechner in
  der gewählten DB protokolliert wurde.
- Zeitraum, Zuordnung und weitere DB unter „Optionen“ (Standard: 7 Tage).
""",
}
HILFE_KEYS = ("Keys kommen aus der `.env` im Ordner von llm-core oder aus der "
              "Windows-Anmeldeinformationsverwaltung (Dienst `llm-core`).")

st.session_state.setdefault("opt_gelb", STANDARD.gelb)
st.session_state.setdefault("opt_tage", STANDARD_TAGE)
st.session_state.setdefault("opt_zuordnung", "")
st.session_state.setdefault("opt_db_extra", "")


def _theme_farbe() -> str:
    """primaryColor aus .streamlit/config.toml, passend zu hell/dunkel (Diagramme übernehmen sie nicht)."""
    try:
        dunkel = st.context.theme.type == "dark"
    except Exception:
        dunkel = False
    return THEME_FARBE["dark" if dunkel else "light"]


def _md(text: str) -> str:
    return text.replace("$", "\\$")   # "$" sonst Formel


def _zeile(s: an.KeyStatus, schwellen: an.Schwellen) -> dict:
    anteil = an.anteil(s)
    return {
        "Ampel": an.ampel(s, schwellen),
        "Anbieter": s.anbieter,
        "Name": s.name,
        "Key": s.kennung,
        "aktiv": {True: "ja", False: "nein", None: ""}[s.aktiv],
        "heute": betrag(s.heute),
        "Monat": betrag(s.monat),
        "gesamt": betrag(s.verbrauch_gesamt),
        "Limit": (betrag(s.limit) if s.limit is not None else ("ohne" if s.limit_abfragbar else "–")),
        "Zurücksetzen": s.limit_reset or "",
        "Anteil": f"{round(anteil * 100)} %" if anteil is not None else "",
        "Rest": betrag(s.rest),
        "Fehler": s.fehler or "",
    }


def _tabelle(zeilen: list[dict]):
    """Keys als Tabelle; die Ampel als farbiger Punkt, ihre Bedeutung im Tooltip der Spalte."""
    import pandas as pd

    df = pd.DataFrame(zeilen)
    if df.empty:
        return df
    farben = df["Ampel"].map(AMPEL_FARBE)
    df["Ampel"] = "●"
    return df.style.apply(lambda _: [f"color: {f}" for f in farben], subset=["Ampel"])


def _status_text(s: an.KeyStatus | None) -> str:
    if s is None:
        return "–"
    limit = f"Limit {betrag(s.limit)}" if s.limit is not None else "ohne Limit"
    aktiv = {True: "aktiv", False: "deaktiviert", None: "?"}[s.aktiv]
    return f"{s.name} ({s.kennung}): {limit}{' / ' + s.limit_reset if s.limit_reset else ''}, {aktiv}"


def _plan(aktion: str, titel: str, vorher: str, nachher: str, **parameter) -> None:
    st.session_state["plan"] = {"aktion": aktion, "titel": titel, "vorher": vorher, "nachher": nachher,
                                "parameter": parameter}
    st.rerun()


# -- Titel und Knopfleiste ------------------------------------------------------

st.title("LLM-Kosten")

with button_bar("konsole"):
    reiter = st.session_state.get("reiter", REITER[0])
    with help_popover("konsole", HILFE.get(reiter, HILFE[REITER[0]])):
        st.caption(HILFE_KEYS)
    if st.button("Aktualisieren", icon=":material/refresh:", key="aktualisieren"):
        an.cache_leeren()
        st.toast("Neu beim Anbieter abgefragt", icon=":material/refresh:")
    aktiv = [
        f"orange ab {round(st.session_state.opt_gelb * 100)} %" if st.session_state.opt_gelb != STANDARD.gelb else "",
        f"{st.session_state.opt_tage} {'Tag' if st.session_state.opt_tage == 1 else 'Tage'}" if st.session_state.opt_tage != STANDARD_TAGE else "",
        st.session_state.opt_zuordnung.strip(),
    ]
    with options_popover(*aktiv):
        st.slider("Ampel orange ab (Anteil am Limit)", 0.5, 1.0, step=0.05, format="%.2f", key="opt_gelb")
        st.caption("Details lokal")
        st.number_input("Tage", min_value=1, max_value=3650, step=1, key="opt_tage")
        st.text_input("Zuordnung (tag), leer = alle", key="opt_zuordnung")
        st.text_input("Weitere DB (Ordner oder costs.sqlite)", key="opt_db_extra")
    # Seiteneigenes zuletzt: Dashboards der Anbieter (öffnen in neuem Tab)
    for titel, url in DASHBOARDS.items():
        st.link_button(titel, url, icon=":material/open_in_new:", help=url)

schwellen = an.Schwellen(gelb=st.session_state.opt_gelb, rot=max(STANDARD.rot, st.session_state.opt_gelb))

openrouter = an.OpenRouter()
anthropic = an.Anthropic()

uebersicht, verwaltung, lokal = st.tabs(REITER, key="reiter", on_change="rerun")

# -- Übersicht ------------------------------------------------------------------

with uebersicht:
    if not openrouter.hat_provisioning:
        st.info("Kein Provisioning-Key (`OPENROUTER_PROVISIONING_KEY`): angezeigt werden nur die Keys aus der "
                "Umgebung (`OPENROUTER_API_KEY*`). „OpenRouter verwalten“ ist ausgeschaltet.", icon=":material/info:")
    konto = openrouter.konto()
    if konto is not None:
        if konto.fehler:
            st.warning(_md(f"OpenRouter-Guthaben: {konto.fehler}"), icon=":material/warning:")
        else:
            a, b, c = st.columns(3)
            a.metric("Guthaben eingezahlt", betrag(konto.guthaben))
            b.metric("verbraucht", betrag(konto.verbraucht))
            c.metric("Rest", betrag(konto.rest))

    keys = openrouter.keys() + anthropic.keys()
    ohne_limit = [s for s in keys if not s.fehler and s.limit is None and s.limit_abfragbar]
    if ohne_limit:
        st.error(_md("Keys ohne Limit (Risiko): " + ", ".join(f"{s.name} ({s.kennung})" for s in ohne_limit)),
                 icon=":material/error:")
    st.dataframe(_tabelle([_zeile(s, schwellen) for s in keys]), width="stretch", hide_index=True,
                 column_config={"Ampel": st.column_config.TextColumn(
                     "", width="small", help=" · ".join(AMPEL_TEXT.values()))})

# -- Verwaltung -----------------------------------------------------------------

with verwaltung:
    neuer = st.session_state.pop("neuer_key", None)
    if neuer:
        st.success("Neuer Key angelegt. Er wird **nur jetzt** angezeigt — sofort in die `.env` der App bzw. "
                   "den Passwortmanager übernehmen. Nach der nächsten Aktion ist er hier weg.",
                   icon=":material/key:")
        st.code(neuer, language=None)
    ergebnis: an.Aenderung | None = st.session_state.pop("ergebnis", None)
    if ergebnis is not None:
        if ergebnis.ok:
            st.toast(_md(f"Erledigt ({ergebnis.aktion}): {_status_text(ergebnis.nachher)}"),
                     icon=":material/check_circle:", duration="long")
        else:
            st.error(_md(f"Fehlgeschlagen ({ergebnis.aktion}): {ergebnis.fehler}"), icon=":material/error:")

    if not openrouter.hat_provisioning:
        st.info("Die Verwaltung der OpenRouter-Keys braucht den Provisioning-Key (siehe „Hilfe“).", icon=":material/info:")
    else:
        verwaltbar = [s for s in openrouter.alle_keys() if s.id]
        plan = st.session_state.get("plan")

        if plan:
            with st.container(border=True, key="panel_bestaetigen"):
                panel_header("Bitte bestätigen")
                st.markdown(_md(f"**Aktion:** {plan['titel']}  \n**Vorher:** {plan['vorher']}  \n"
                                f"**Nachher:** {plan['nachher']}"))
                with st.container(horizontal=True, gap="small"):
                    if st.button("Bestätigen", icon=":material/check:", type="primary", key="plan_ja"):
                        st.session_state.pop("plan")
                        p = plan["parameter"]
                        if plan["aktion"] == "limit":
                            erg = openrouter.limit_aendern(p["id"], p["limit"], limit_reset=p["reset"],
                                                           bestaetigt=True)
                        elif plan["aktion"] == "aktiv":
                            erg = openrouter.key_aktivieren(p["id"], p["aktiv"], bestaetigt=True)
                        else:
                            erg = openrouter.key_anlegen(p["name"], p["limit"], limit_reset=p["reset"],
                                                         bestaetigt=True)
                        if erg.klartext:
                            st.session_state["neuer_key"] = erg.klartext
                            erg.klartext = None
                        st.session_state["ergebnis"] = erg
                        st.rerun()
                    if st.button("Abbrechen", icon=":material/close:", key="plan_nein"):
                        st.session_state.pop("plan")
                        st.rerun()
        else:
            if verwaltbar:
                st.subheader("Bestehender Key")
                wahl = st.selectbox("Key", verwaltbar, format_func=lambda s: f"{s.name} ({s.kennung})")
                spalte_limit, spalte_aktiv = st.columns(2)
                with spalte_limit:
                    ohne = st.checkbox("ohne Limit (nicht empfohlen)", value=False)
                    neues_limit = st.number_input("Neues Limit (USD)", min_value=0.0, step=1.0,
                                                  value=float(wahl.limit or 10.0), disabled=ohne)
                    reset_name = st.selectbox("Zurücksetzen", list(RESETS),
                                              index=list(RESETS.values()).index(wahl.limit_reset)
                                              if wahl.limit_reset in RESETS.values() else 0)
                    if st.button("Limit ändern …", icon=":material/edit:"):
                        limit = None if ohne else float(neues_limit)
                        reset = RESETS[reset_name]
                        _plan("limit", f"Limit von {wahl.name} ändern", _status_text(wahl),
                              f"Limit {betrag(limit) if limit is not None else 'ohne Limit'}"
                              f"{' / ' + reset if reset else ''}",
                              id=wahl.id, limit=limit, reset=reset)
                with spalte_aktiv:
                    aktiv_neu = wahl.aktiv is False
                    if st.button("Key aktivieren …" if aktiv_neu else "Key deaktivieren …",
                                 icon=":material/play_arrow:" if aktiv_neu else ":material/block:"):
                        _plan("aktiv", f"{wahl.name} {'aktivieren' if aktiv_neu else 'deaktivieren'}",
                              _status_text(wahl), "aktiv" if aktiv_neu else "deaktiviert",
                              id=wahl.id, aktiv=aktiv_neu)
            else:
                st.caption("Keine verwaltbaren Keys gefunden (Fehler siehe Übersicht).")

            st.subheader("Neuer Key")
            with st.form("anlegen"):
                name = st.text_input("Name", placeholder="z. B. probatio-visibilia")
                limit_neu = st.number_input("Limit (USD)", min_value=0.0, step=1.0, value=10.0)
                reset_neu = st.selectbox("Zurücksetzen", list(RESETS))
                if st.form_submit_button("Anlegen …", icon=":material/add:") and name.strip():
                    reset = RESETS[reset_neu]
                    _plan("anlegen", f"Key „{name.strip()}“ anlegen", "(kein Key)",
                          f"Limit {betrag(limit_neu)}{' / ' + reset if reset else ''}",
                          name=name.strip(), limit=float(limit_neu), reset=reset)

# -- Details lokal --------------------------------------------------------------

with lokal:
    kandidaten = daten.db_kandidaten()
    eigene = st.session_state.opt_db_extra.strip()
    if eigene:
        pfad = Path(eigene).expanduser()
        pfad = pfad if pfad.suffix == ".sqlite" else pfad / daten.DB_NAME
        if pfad.is_file() and pfad.resolve() not in kandidaten:
            kandidaten.insert(0, pfad.resolve())
        elif not pfad.is_file():
            st.warning(f"Keine Kosten-DB unter {pfad}", icon=":material/warning:")
    if not kandidaten:
        st.info("Keine lokale Kosten-DB gefunden (`~/.llm_core`, `~/.memoria`, `LLM_CORE_DATA_DIR`).",
                icon=":material/info:")
    else:
        tage = int(st.session_state.opt_tage)
        zuordnung = st.session_state.opt_zuordnung.strip()
        db = st.selectbox("Datenbank", kandidaten, format_func=str)
        d = daten.details(db, tage, zuordnung or None)
        if d["fehler"]:
            st.error(d["fehler"], icon=":material/error:")
        else:
            st.metric(f"Summe {tage} {'Tag' if tage == 1 else 'Tage'} (Schätzung)", betrag(d["summe"]))
            if d["je_tag"]:
                import pandas as pd
                st.bar_chart(pd.DataFrame(d["je_tag"]).set_index("datum")["kosten_usd"],
                             color=_theme_farbe())
            st.markdown("**Je Modell**")
            st.dataframe(d["je_modell"], width="stretch", hide_index=True)
            st.markdown("**Je Zuordnung**")
            st.dataframe(d["je_tag_zuordnung"], width="stretch", hide_index=True)
