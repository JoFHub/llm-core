"""
Seitenleisten-Baustein für Streamlit-Apps (Probatio, Memoria, Fabula): Kosten in ein bis zwei Zeilen.

    from llm_core.ui import kosten_seitenleiste
    kosten_seitenleiste(key_env="OPENROUTER_API_KEY_VISIBILIA", titel="Kosten Visibilia")

Für Apps direkt bei Anthropic (kein Limit und kein Verbrauch je Key abfragbar) die
aus der Kosten-DB berechneten Kosten:

    from llm_core.ui import kosten_seitenleiste_berechnet
    kosten_seitenleiste_berechnet(provider="anthropic", titel="Kosten Fabula", tag="fabula")

Beträge nur in Euro (wie die Kosten-App): Der Anbieter rechnet in USD, umgerechnet wird zum EZB-Kurs
(`kosten_app.kurs`, höchstens zweimal am Tag abgefragt; fest mit `LLM_CORE_EUR_KURS=0,92`). Ohne Kurs nie Dollar
als Ersatz, sondern nur der Anteil am Limit bzw. ein Hinweis.

Streamlit ist optional (`pip install "llm-core[ui]"`) und wird erst im Aufruf importiert. Die Zeilen
entstehen im Server-Prozess; im HTML steht nur der Text, nie ein Key (auch nicht gekürzt).
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from llm_core import anbieter as _anbieter
from llm_core import cost_tracker as _cost_tracker
from llm_core.kosten_app.kurs import Kurs, KursQuelle

# Euro-Kurs mit Zwischenspeicher im Prozess (eine Abfrage für alle Sitzungen einer App)
_KURS = KursQuelle()
_OHNE_KURS = "kein Euro-Kurs"

# Material-Symbole in Streamlit-Markdown statt farbiger Emojis (GUI-Standard wie Memoria)
AMPEL_ZEICHEN = {
    _anbieter.GRUEN: ":green[:material/check_circle:]",
    _anbieter.GELB: ":orange[:material/warning:]",
    _anbieter.ROT: ":red[:material/error:]",
    _anbieter.GRAU: ":gray[:material/help:]",
}


def betrag(usd: float | None, waehrung: str = "$") -> str:
    """4.1234 -> '4,12 $'; kleine Beträge mit mehr Stellen ('0,004 $'), None -> '–'. waehrung z. B. '€'."""
    if usd is None:
        return "–"
    stellen = 2 if abs(usd) >= 0.1 or usd == 0 else 3
    zahl = f"{usd:,.{stellen}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{zahl} {waehrung}"


def eur(usd: float | None, kurs: Kurs | None) -> str:
    """USD-Betrag des Anbieters als Euro ('3,86 €'); ohne Kurs oder Wert '–'."""
    if usd is None or kurs is None:
        return "–"
    return betrag(usd * kurs.wert, "€")


def _kurs(kurs: "Kurs | None | bool") -> Kurs | None:
    """kurs=True (Standard): aktueller Kurs aus dem Zwischenspeicher; sonst der übergebene (Tests)."""
    return _KURS.kurs() if kurs is True else kurs or None


def kosten_zeilen(
    key_env: str = "OPENROUTER_API_KEY",
    adapter: "_anbieter.OpenRouter | None" = None,
    schwellen: "_anbieter.Schwellen | None" = None,
    kurs: "Kurs | None | bool" = True,
) -> list[str]:
    """Die Zeilen des Bausteins (ohne Streamlit, testbar).

    Kosten je Key, wie der Anbieter sie abrechnet (über alle Rechner): Verbrauch von Limit mit Ampel; heute/Monat
    und Einzelheiten nur in der Kosten-App. Ohne Key oder bei Fehler eine graue Zeile, keine Schätzung."""
    key = os.environ.get(key_env, "").strip()
    zeilen: list[str] = []
    if key:
        quelle = adapter or _anbieter.OpenRouter(api_keys={}, provisioning="")
        status = quelle.key_status(key, name=key_env)
        if not status.fehler:
            zeichen = AMPEL_ZEICHEN[_anbieter.ampel(status, schwellen)]
            verbraucht = status.verbrauch_im_limit
            k = _kurs(kurs)
            if status.limit is None:
                zeilen.append(f"{zeichen} {eur(verbraucht, k)} · ohne Limit" if k
                              else f"{zeichen} ohne Limit ({_OHNE_KURS})")
            else:
                anteil = _anbieter.anteil(status) or 0.0
                zeitraum = {"daily": " heute", "weekly": " Woche", "monthly": " Monat"}.get(
                    status.limit_reset or "", "")
                zeilen.append(f"{zeichen} {eur(verbraucht, k)} von {eur(status.limit, k)}{zeitraum} "
                              f"({round(anteil * 100)} %)" if k
                              else f"{zeichen} {round(anteil * 100)} % des Limits{zeitraum} ({_OHNE_KURS})")
            return zeilen
        zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} Anbieter: {status.fehler}")
    else:
        zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} kein Key ({key_env})")
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
        text = "  \n".join(zeilen).replace("$", "\\$")
        st.caption(f"**{titel}**  \n{text}" if titel else text)
    return zeilen


MONATSNAMEN = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
               "September", "Oktober", "November", "Dezember")


def heute_monat_zeile(heute: float | None, monat: float | None, monat_nr: int,
                      kurs: "Kurs | None | bool" = True) -> str:
    """'heute 0,41 € · Oktober 2,49 €' (Seitenleiste der Apps; Beträge in USD, angezeigt in Euro)."""
    k = _kurs(kurs)
    if not k:
        return f"heute – · {MONATSNAMEN[monat_nr - 1]} – ({_OHNE_KURS})"
    return f"heute {eur(heute, k)} · {MONATSNAMEN[monat_nr - 1]} {eur(monat, k)}"


def berechnet_zeilen(
    provider: str = "anthropic",
    tag: str | None = None,
    zeitzone: str = "Europe/Berlin",
    jetzt: datetime | None = None,
    kurs: "Kurs | None | bool" = True,
) -> list[str]:
    """Zeile des Bausteins für berechnete Kosten (ohne Streamlit, testbar): heute und
    laufender Monat aus der Kosten-DB dieses Rechners (Token-Zahlen des Anbieters mal
    Preisliste), in Euro umgerechnet, z. B. "heute 0,41 € · Oktober 2,49 €"."""
    jetzt = (jetzt or datetime.now(ZoneInfo(zeitzone))).astimezone(ZoneInfo(zeitzone))
    tag_beginn = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
    monat_beginn = tag_beginn.replace(day=1)
    heute = _cost_tracker.cost_since(tag_beginn, provider=provider, tag=tag)
    monat = _cost_tracker.cost_since(monat_beginn, provider=provider, tag=tag)
    return [heute_monat_zeile(heute, monat, jetzt.month, kurs)]


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
        text = "  \n".join(zeilen).replace("$", "\\$")
        st.caption(f"**{titel}**  \n{text}" if titel else text)
    return zeilen
