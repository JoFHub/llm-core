"""Seite „OpenRouter“ der Konsole: Keys der Apps über OpenRouter (Memoria, Probatio).

Keys erscheinen nur gekürzt; einzige Ausnahme ist der Klartext eines neu angelegten Keys, genau einmal.
"""
from __future__ import annotations

import streamlit as st

from llm_core import anbieter as an
from llm_core.konsole import _gemeinsam as g
from llm_core.konsole._widgets import button_bar, help_popover, options_popover, panel_header
from llm_core.ui import betrag

RESETS = {"nie (Limit gilt insgesamt)": None, "täglich": "daily", "wöchentlich": "weekly", "monatlich": "monthly"}

# Ampel in der Tabelle: typografischer Punkt, Farbe wie --lc-… in _widgets.CSS (Tabellen sind ein Canvas,
# CSS-Variablen greifen dort nicht)
AMPEL_FARBE = {an.GRUEN: "#2e7d32", an.GELB: "#ef6c00", an.ROT: "#c62828", an.GRAU: "#888"}
AMPEL_TEXT = {an.GRUEN: "im Limit", an.GELB: "Limit fast erreicht", an.ROT: "Limit erreicht oder ohne Limit",
              an.GRAU: "unbekannt"}

DASHBOARD = "https://openrouter.ai/activity"

# Text im Fenster „Hilfe“ je Reiter (Reiter mit key + on_change="rerun", Muster wie Memoria → Konto)
REITER = ["Übersicht", "Verwalten", "Details lokal"]
HILFE = {
    "Übersicht": f"""\
Keys der Apps über OpenRouter ({g.APPS['openrouter']}): Verbrauch und Limits direkt bei OpenRouter, in USD,
zwischengespeichert bis 60 s.

- **Ampel:** grün im Limit, orange ab der Schwelle unter „Optionen“, rot bei erreichtem Limit oder
  Key ohne Limit, grau bei Fehler.
- **Guthaben:** eingezahlt, verbraucht, Rest des Kontos.
- Mit Provisioning-Key (`OPENROUTER_PROVISIONING_KEY`) alle Keys des Kontos und das Guthaben, sonst nur die
  Keys aus der Umgebung (`OPENROUTER_API_KEY*`).
""",
    "Verwalten": """\
Limit ändern, Key aktivieren oder deaktivieren, neuen Key anlegen.

- Jede Aktion zeigt vorher/nachher und läuft erst nach „Bestätigen“.
- Der Klartext eines neuen Keys erscheint genau einmal: sofort in die `.env` der App bzw. den
  Passwortmanager übernehmen.
- Braucht den Provisioning-Key (`OPENROUTER_PROVISIONING_KEY`).
""",
    "Details lokal": g.HILFE_DETAILS.format(
        anbieter="OpenRouter", schaetzung="Echter Preis je Aufruf, wie OpenRouter ihn meldet"),
}


def _zeile(s: an.KeyStatus, schwellen: an.Schwellen) -> dict:
    anteil = an.anteil(s)
    return {
        "Ampel": an.ampel(s, schwellen),
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

st.title("OpenRouter")

with button_bar("openrouter"):
    reiter = st.session_state.get("or_reiter", REITER[0])
    with help_popover("openrouter", HILFE.get(reiter, HILFE[REITER[0]])):
        st.caption(g.HILFE_KEYS)
    if st.button("Aktualisieren", icon=":material/refresh:", key="aktualisieren"):
        an.cache_leeren()
        st.toast("Neu bei OpenRouter abgefragt", icon=":material/refresh:")
    gelb = st.session_state.or_opt_gelb
    aktiv = [f"orange ab {round(gelb * 100)} %" if gelb != g.STANDARD.gelb else "",
             *g.optionen_details_aktiv("or")]
    with options_popover(*aktiv):
        st.slider("Ampel orange ab (Anteil am Limit)", 0.5, 1.0, step=0.05, format="%.2f", key="or_opt_gelb")
        st.caption("Details lokal")
        g.optionen_details("or")
    st.link_button("Dashboard", DASHBOARD, icon=":material/open_in_new:", help=DASHBOARD)

schwellen = g.schwellen()
openrouter = an.OpenRouter()
konto = openrouter.konto()
keys = openrouter.keys()

uebersicht, verwaltung, lokal = st.tabs(REITER, key="or_reiter", on_change="rerun")

# -- Übersicht ------------------------------------------------------------------

with uebersicht:
    if not openrouter.hat_provisioning:
        st.info("Kein Provisioning-Key (`OPENROUTER_PROVISIONING_KEY`): angezeigt werden nur die Keys aus der "
                "Umgebung (`OPENROUTER_API_KEY*`). „Verwalten“ ist ausgeschaltet.", icon=":material/info:")
    if konto is not None:
        if konto.fehler:
            st.warning(g.md(f"Guthaben: {konto.fehler}"), icon=":material/warning:")
        else:
            a, b, c = st.columns(3)
            a.metric("Guthaben · eingezahlt", betrag(konto.guthaben))
            b.metric("Guthaben · verbraucht", betrag(konto.verbraucht))
            c.metric("Guthaben · Rest", betrag(konto.rest))

    ohne_limit = [s for s in keys if not s.fehler and s.limit is None and s.limit_abfragbar]
    if ohne_limit:
        st.error(g.md("Keys ohne Limit (Risiko): " + ", ".join(f"{s.name} ({s.kennung})" for s in ohne_limit)),
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
            st.toast(g.md(f"Erledigt ({ergebnis.aktion}): {_status_text(ergebnis.nachher)}"),
                     icon=":material/check_circle:", duration="long")
        else:
            st.error(g.md(f"Fehlgeschlagen ({ergebnis.aktion}): {ergebnis.fehler}"), icon=":material/error:")

    if not openrouter.hat_provisioning:
        st.info("Die Verwaltung der Keys braucht den Provisioning-Key (siehe „Hilfe“).", icon=":material/info:")
    else:
        verwaltbar = [s for s in openrouter.alle_keys() if s.id]
        plan = st.session_state.get("plan")

        if plan:
            with st.container(border=True, key="panel_bestaetigen"):
                panel_header("Bitte bestätigen")
                st.markdown(g.md(f"**Aktion:** {plan['titel']}  \n**Vorher:** {plan['vorher']}  \n"
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
    g.details_lokal("or", "openrouter")
