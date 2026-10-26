"""Streamlit-Seite der Kosten-Konsole. Start über `python -m llm_core.konsole` (nur localhost).

Keys erscheinen nur gekürzt; einzige Ausnahme ist der Klartext eines neu angelegten Keys, genau einmal.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from llm_core import anbieter as an  # lädt über llm_core.config auch die .env
from llm_core.konsole import daten
from llm_core.ui import AMPEL_ZEICHEN, betrag

st.set_page_config(page_title="LLM-Kosten", page_icon="💶", layout="wide")

RESETS = {"nie (Limit gilt insgesamt)": None, "täglich": "daily", "wöchentlich": "weekly", "monatlich": "monthly"}


def _md(text: str) -> str:
    return text.replace("$", "\\$")   # "$" sonst Formel


def _zeile(s: an.KeyStatus, schwellen: an.Schwellen) -> dict:
    anteil = an.anteil(s)
    return {
        "": AMPEL_ZEICHEN[an.ampel(s, schwellen)],
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


def _status_text(s: an.KeyStatus | None) -> str:
    if s is None:
        return "–"
    limit = betrag(s.limit) if s.limit is not None else "ohne Limit"
    aktiv = {True: "aktiv", False: "deaktiviert", None: "?"}[s.aktiv]
    return f"{s.name} ({s.kennung}): Limit {limit}{' / ' + s.limit_reset if s.limit_reset else ''}, {aktiv}"


# -- Seitenleiste ---------------------------------------------------------------

with st.sidebar:
    st.header("LLM-Kosten")
    if st.button("Aktualisieren", use_container_width=True):
        an.cache_leeren()
    gelb = st.slider("Ampel gelb ab", 0.5, 1.0, an.Schwellen.from_env().gelb, 0.05, format="%.2f")
    schwellen = an.Schwellen(gelb=gelb, rot=max(an.Schwellen.from_env().rot, gelb))

openrouter = an.OpenRouter()
anthropic = an.Anthropic()

uebersicht, verwaltung, lokal = st.tabs(["Übersicht", "Verwaltung", "Details lokal"])

# -- Übersicht ------------------------------------------------------------------

with uebersicht:
    if not openrouter.hat_provisioning:
        st.info("Kein Provisioning-Key (`OPENROUTER_PROVISIONING_KEY`): angezeigt werden nur die Keys aus der "
                "Umgebung (`OPENROUTER_API_KEY*`). Verwaltung ist ausgeschaltet.")
    konto = openrouter.konto()
    if konto is not None:
        if konto.fehler:
            st.warning(_md(f"OpenRouter-Guthaben: {konto.fehler}"))
        else:
            a, b, c = st.columns(3)
            a.metric("Guthaben eingezahlt", betrag(konto.guthaben))
            b.metric("verbraucht", betrag(konto.verbraucht))
            c.metric("Rest", betrag(konto.rest))

    keys = openrouter.keys() + anthropic.keys()
    ohne_limit = [s for s in keys if not s.fehler and s.limit is None and s.limit_abfragbar]
    if ohne_limit:
        st.error(_md("Keys ohne Limit (Risiko): " + ", ".join(f"{s.name} ({s.kennung})" for s in ohne_limit)))
    st.dataframe([_zeile(s, schwellen) for s in keys], use_container_width=True, hide_index=True)
    st.caption(_md("Echte Werte des Anbieters (USD). Anthropic: Kosten der Organisation über die Admin-API "
                   "(`ANTHROPIC_ADMIN_KEY`), ohne Limit-Abfrage. Stand: zwischengespeichert bis 60 s."))

# -- Verwaltung -----------------------------------------------------------------

with verwaltung:
    neuer = st.session_state.pop("neuer_key", None)
    if neuer:
        st.success("Neuer Key angelegt. Er wird **nur jetzt** angezeigt — sofort in die `.env` der App bzw. "
                   "den Passwortmanager übernehmen. Nach der nächsten Aktion ist er hier weg.")
        st.code(neuer, language=None)
    ergebnis: an.Aenderung | None = st.session_state.pop("ergebnis", None)
    if ergebnis is not None:
        if ergebnis.ok:
            st.success(_md(f"Erledigt ({ergebnis.aktion}). Vorher: {_status_text(ergebnis.vorher)} — "
                           f"nachher: {_status_text(ergebnis.nachher)}"))
        else:
            st.error(_md(f"Fehlgeschlagen ({ergebnis.aktion}): {ergebnis.fehler}"))

    if not openrouter.hat_provisioning:
        st.info("Verwaltung braucht den Provisioning-Key (siehe README).")
    else:
        verwaltbar = [s for s in openrouter.alle_keys() if s.id]
        plan = st.session_state.get("plan")

        if plan:
            st.subheader("Bitte bestätigen")
            st.write(_md(f"**Aktion:** {plan['titel']}"))
            st.write(_md(f"**Vorher:** {plan['vorher']}"))
            st.write(_md(f"**Nachher:** {plan['nachher']}"))
            ja, nein = st.columns(2)
            if ja.button("Bestätigen", type="primary", use_container_width=True):
                st.session_state.pop("plan")
                p = plan["parameter"]
                if plan["aktion"] == "limit":
                    erg = openrouter.limit_aendern(p["id"], p["limit"], limit_reset=p["reset"], bestaetigt=True)
                elif plan["aktion"] == "aktiv":
                    erg = openrouter.key_aktivieren(p["id"], p["aktiv"], bestaetigt=True)
                else:
                    erg = openrouter.key_anlegen(p["name"], p["limit"], limit_reset=p["reset"], bestaetigt=True)
                if erg.klartext:
                    st.session_state["neuer_key"] = erg.klartext
                    erg.klartext = None
                st.session_state["ergebnis"] = erg
                st.rerun()
            if nein.button("Abbrechen", use_container_width=True):
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
                    if st.button("Limit ändern …"):
                        limit = None if ohne else float(neues_limit)
                        reset = RESETS[reset_name]
                        nachher = (f"Limit {betrag(limit) if limit is not None else 'ohne Limit'}"
                                   f"{' / ' + reset if reset else ''}")
                        st.session_state["plan"] = {
                            "aktion": "limit", "titel": f"Limit von {wahl.name} ändern",
                            "vorher": _status_text(wahl), "nachher": nachher,
                            "parameter": {"id": wahl.id, "limit": limit, "reset": reset}}
                        st.rerun()
                with spalte_aktiv:
                    aktiv_neu = not (wahl.aktiv is not False)
                    if st.button("Key aktivieren …" if aktiv_neu else "Key deaktivieren …"):
                        st.session_state["plan"] = {
                            "aktion": "aktiv", "titel": f"{wahl.name} {'aktivieren' if aktiv_neu else 'deaktivieren'}",
                            "vorher": _status_text(wahl), "nachher": "aktiv" if aktiv_neu else "deaktiviert",
                            "parameter": {"id": wahl.id, "aktiv": aktiv_neu}}
                        st.rerun()
            else:
                st.caption("Keine verwaltbaren Keys gefunden (Fehler siehe Übersicht).")

            st.subheader("Neuer Key")
            with st.form("anlegen"):
                name = st.text_input("Name", placeholder="z. B. probatio-visibilia")
                limit_neu = st.number_input("Limit (USD)", min_value=0.0, step=1.0, value=10.0)
                reset_neu = st.selectbox("Zurücksetzen", list(RESETS))
                if st.form_submit_button("Anlegen …") and name.strip():
                    reset = RESETS[reset_neu]
                    st.session_state["plan"] = {
                        "aktion": "anlegen", "titel": f"Key „{name.strip()}“ anlegen",
                        "vorher": "(kein Key)",
                        "nachher": f"Limit {betrag(limit_neu)}{' / ' + reset if reset else ''}",
                        "parameter": {"name": name.strip(), "limit": float(limit_neu), "reset": reset}}
                    st.rerun()

# -- Details lokal --------------------------------------------------------------

with lokal:
    kandidaten = daten.db_kandidaten()
    eigene = st.text_input("Weitere DB (Ordner oder costs.sqlite)", "")
    if eigene.strip():
        pfad = Path(eigene.strip()).expanduser()
        pfad = pfad if pfad.suffix == ".sqlite" else pfad / daten.DB_NAME
        if pfad.is_file() and pfad.resolve() not in kandidaten:
            kandidaten.insert(0, pfad.resolve())
        elif not pfad.is_file():
            st.warning(f"Keine Kosten-DB unter {pfad}")
    if not kandidaten:
        st.info("Keine lokale Kosten-DB gefunden (`~/.llm_core`, `~/.memoria`, `LLM_CORE_DATA_DIR`).")
    else:
        links, mitte, rechts = st.columns([3, 1, 2])
        db = links.selectbox("Datenbank", kandidaten, format_func=str)
        tage = mitte.number_input("Tage", min_value=1, max_value=3650, value=30, step=1)
        zuordnung = rechts.text_input("Zuordnung (tag), leer = alle", "")
        d = daten.details(db, int(tage), zuordnung if zuordnung else None)
        if d["fehler"]:
            st.error(d["fehler"])
        else:
            st.metric(f"Summe {int(tage)} Tage (Schätzung)", betrag(d["summe"]))
            if d["je_tag"]:
                import pandas as pd
                st.bar_chart(pd.DataFrame(d["je_tag"]).set_index("datum")["kosten_usd"])
            st.write("**Je Modell**")
            st.dataframe(d["je_modell"], use_container_width=True, hide_index=True)
            st.write("**Je Zuordnung**")
            st.dataframe(d["je_tag_zuordnung"], use_container_width=True, hide_index=True)
        st.caption(_md("Lokale Werte sind Schätzungen aus der Preistabelle (OpenRouter: echter Preis je Aufruf) "
                       "und zeigen nur, was auf diesem Rechner in dieser DB protokolliert wurde."))
