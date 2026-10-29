"""
Seitenleisten-Baustein für Streamlit-Apps (Probatio, Memoria, Fabula): Kosten in ein bis zwei Zeilen.

    from llm_core.ui import kosten_seitenleiste
    kosten_seitenleiste(key_env="OPENROUTER_API_KEY_VISIBILIA", titel="Kosten Visibilia")

Für Apps direkt bei Anthropic (kein Limit und kein Verbrauch je Key abfragbar) die
aus der Kosten-DB berechneten Kosten:

    from llm_core.ui import kosten_seitenleiste_berechnet
    kosten_seitenleiste_berechnet(provider="anthropic", titel="Kosten Fabula", tag="fabula")

Streamlit ist optional (`pip install "llm-core[ui]"`) und wird erst im Aufruf importiert. Die Zeilen
entstehen im Server-Prozess; im HTML steht nur der Text, nie ein Key (auch nicht gekürzt).
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from llm_core import anbieter as _anbieter
from llm_core import cost_tracker as _cost_tracker

# Material-Symbole in Streamlit-Markdown statt farbiger Emojis (GUI-Standard wie Memoria)
AMPEL_ZEICHEN = {
    _anbieter.GRUEN: ":green[:material/check_circle:]",
    _anbieter.GELB: ":orange[:material/warning:]",
    _anbieter.ROT: ":red[:material/error:]",
    _anbieter.GRAU: ":gray[:material/help:]",
}


def betrag(usd: float | None) -> str:
    """4.1234 -> '4,12 $'; kleine Beträge mit mehr Stellen ('0,004 $'), None -> '–'."""
    if usd is None:
        return "–"
    stellen = 2 if abs(usd) >= 0.1 or usd == 0 else 3
    return f"{usd:,.{stellen}f} $".replace(",", " ").replace(".", ",").replace(" ", ".")


def kosten_zeilen(
    key_env: str = "OPENROUTER_API_KEY",
    adapter: "_anbieter.OpenRouter | None" = None,
    schwellen: "_anbieter.Schwellen | None" = None,
) -> list[str]:
    """Die Zeilen des Bausteins (ohne Streamlit, testbar).

    Kosten je Key, wie der Anbieter sie abrechnet (über alle Rechner): Verbrauch von Limit mit Ampel; heute/Monat
    und Einzelheiten aus der Kosten-DB nur in der Konsole. Ohne Key oder bei Fehler eine graue Zeile, keine Schätzung."""
    key = os.environ.get(key_env, "").strip()
    zeilen: list[str] = []
    if key:
        quelle = adapter or _anbieter.OpenRouter(api_keys={}, provisioning="")
        status = quelle.key_status(key, name=key_env)
        if not status.fehler:
            zeichen = AMPEL_ZEICHEN[_anbieter.ampel(status, schwellen)]
            verbraucht = status.verbrauch_im_limit
            if status.limit is None:
                zeilen.append(f"{zeichen} {betrag(verbraucht)} · ohne Limit")
            else:
                anteil = _anbieter.anteil(status) or 0.0
                zeitraum = {"daily": " heute", "weekly": " Woche", "monthly": " Monat"}.get(
                    status.limit_reset or "", "")
                zeilen.append(f"{zeichen} {betrag(verbraucht)} von {betrag(status.limit)}{zeitraum} "
                              f"({round(anteil * 100)} %)")
            return zeilen
        zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} Anbieter: {status.fehler}")
    else:
        zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} kein Key ({key_env})")
    return zeilen


def kurzstatus_zeilen(
    konto: "_anbieter.KontoStatus | None",
    keys: "list[_anbieter.KeyStatus]",
    schwellen: "_anbieter.Schwellen | None" = None,
) -> list[str]:
    """Kurzstatus der Konsole (Seitenleiste): Guthaben, Ampel je Key gezählt, Keys ohne Limit, Verbrauch.

    Markdown-Zeilen für Streamlit (Material-Symbole), ohne Streamlit testbar. Keys nur mit Namen."""
    zeilen: list[str] = []
    if konto is not None:
        if konto.fehler:
            zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} Guthaben: {konto.fehler}")
        else:
            zeilen.append(f"Rest OpenRouter: **{betrag(konto.rest)}**")
    zaehler: dict[str, int] = {}
    for s in keys:
        a = _anbieter.ampel(s, schwellen)
        zaehler[a] = zaehler.get(a, 0) + 1
    reihenfolge = (_anbieter.ROT, _anbieter.GELB, _anbieter.GRUEN, _anbieter.GRAU)
    teile = [f"{AMPEL_ZEICHEN[a]} {zaehler[a]}" for a in reihenfolge if zaehler.get(a)]
    if teile:
        zeilen.append(f"{len(keys)} {'Key' if len(keys) == 1 else 'Keys'}: " + " · ".join(teile))
    ohne_limit = [s.name for s in keys if not s.fehler and s.limit is None and s.limit_abfragbar]
    if ohne_limit:
        zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.ROT]} ohne Limit: " + ", ".join(ohne_limit))
    heute = [s.heute for s in keys if s.heute is not None]
    monat = [s.monat for s in keys if s.monat is not None]
    if heute or monat:
        zeilen.append(f"heute {betrag(sum(heute)) if heute else '–'} · Monat {betrag(sum(monat)) if monat else '–'}")
    return zeilen


def kurzstatus_anthropic_zeilen(
    keys: "list[_anbieter.KeyStatus]",
    heute: float | None,
    monat: float | None,
    monat_nr: int,
) -> list[str]:
    """Kurzstatus der Konsole für Anthropic (Seitenleiste): berechnete Kosten aus den Kosten-DBs, Kosten der
    Organisation (Admin-API), Zahl der API-Keys. Ohne Ampel: Anthropic liefert je Key weder Verbrauch noch Limit."""
    zeilen = [f"berechnet: {heute_monat_zeile(heute, monat, monat_nr)}"]
    organisation = [s for s in keys if s.name == _anbieter.Anthropic.ORGANISATION]
    api = [s for s in keys if s.name != _anbieter.Anthropic.ORGANISATION]
    for s in organisation:
        if s.fehler:
            zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} Organisation: {s.fehler}")
        else:
            zeilen.append(f"Organisation: {heute_monat_zeile(s.heute, s.monat, monat_nr)}")
    ohne_key = [s for s in api if s.fehler]
    if ohne_key:
        zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} {ohne_key[0].fehler} ({ohne_key[0].name})")
    elif api:
        zeilen.append(f"{len(api)} {'API-Key' if len(api) == 1 else 'API-Keys'}")
    return zeilen


def kosten_seitenleiste(
    key_env: str = "OPENROUTER_API_KEY",
    titel: str | None = "Kosten",
    adapter: "_anbieter.OpenRouter | None" = None,
    schwellen: "_anbieter.Schwellen | None" = None,
) -> list[str]:
    """Schreibt die Kostenzeilen klein in die Streamlit-Seitenleiste und gibt sie zurück."""
    import streamlit as st  # optionale Abhängigkeit, nur hier

    zeilen = kosten_zeilen(key_env, adapter=adapter, schwellen=schwellen)
    with st.sidebar:
        # "$" würde Streamlit als Formel lesen
        text = "  \n".join(zeilen).replace("$", "\\$")
        st.caption(f"**{titel}**  \n{text}" if titel else text)
    return zeilen


MONATSNAMEN = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
               "September", "Oktober", "November", "Dezember")


def heute_monat_zeile(heute: float | None, monat: float | None, monat_nr: int) -> str:
    """'heute 0,45 $ · Oktober 2,74 $' (Seitenleiste der Apps und der Konsole)."""
    return f"heute {betrag(heute)} · {MONATSNAMEN[monat_nr - 1]} {betrag(monat)}"


def berechnet_zeilen(
    provider: str = "anthropic",
    tag: str | None = None,
    zeitzone: str = "Europe/Berlin",
    jetzt: datetime | None = None,
) -> list[str]:
    """Zeile des Bausteins für berechnete Kosten (ohne Streamlit, testbar): heute und
    laufender Monat aus der Kosten-DB dieses Rechners (Token-Zahlen des Anbieters mal
    Preisliste), z. B. "heute 0,45 $ · Oktober 2,74 $"."""
    jetzt = (jetzt or datetime.now(ZoneInfo(zeitzone))).astimezone(ZoneInfo(zeitzone))
    tag_beginn = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
    monat_beginn = tag_beginn.replace(day=1)
    heute = _cost_tracker.cost_since(tag_beginn, provider=provider, tag=tag)
    monat = _cost_tracker.cost_since(monat_beginn, provider=provider, tag=tag)
    return [heute_monat_zeile(heute, monat, jetzt.month)]


def kosten_seitenleiste_berechnet(
    provider: str = "anthropic",
    titel: str | None = "Kosten",
    tag: str | None = None,
    zeitzone: str = "Europe/Berlin",
) -> list[str]:
    """Wie kosten_seitenleiste, aber für einen Anbieter ohne Abfrage je Key (Anthropic):
    Kosten aus der Kosten-DB dieses Rechners. Kein Limit, keine Ampel."""
    import streamlit as st  # optionale Abhängigkeit, nur hier

    zeilen = berechnet_zeilen(provider, tag=tag, zeitzone=zeitzone)
    with st.sidebar:
        # "$" würde Streamlit als Formel lesen
        text = "  \n".join(zeilen).replace("$", "\\$")
        st.caption(f"**{titel}**  \n{text}" if titel else text)
    return zeilen
