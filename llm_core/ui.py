"""
Seitenleisten-Baustein für Streamlit-Apps (Probatio, Memoria, Fabula): Kosten in ein bis drei Zeilen.

    from llm_core.ui import kosten_seitenleiste
    kosten_seitenleiste(key_env="OPENROUTER_API_KEY_VISIBILIA", tag="visibilia", titel="Kosten")

Streamlit ist optional (`pip install "llm-core[ui]"`) und wird erst im Aufruf importiert. Die Zeilen
entstehen im Server-Prozess; im HTML steht nur der Text, nie ein Key (auch nicht gekürzt).
"""
from __future__ import annotations

import os

from llm_core import anbieter as _anbieter
from llm_core import cost_tracker

AMPEL_ZEICHEN = {
    _anbieter.GRUEN: "🟢",
    _anbieter.GELB: "🟡",
    _anbieter.ROT: "🔴",
    _anbieter.GRAU: "⚪",
}


def betrag(usd: float | None) -> str:
    """4.1234 -> '4,12 $'; kleine Beträge mit mehr Stellen ('0,004 $'), None -> '–'."""
    if usd is None:
        return "–"
    stellen = 2 if abs(usd) >= 0.1 or usd == 0 else 3
    return f"{usd:,.{stellen}f} $".replace(",", " ").replace(".", ",").replace(" ", ".")


def kosten_zeilen(
    key_env: str = "OPENROUTER_API_KEY",
    tag: str | None = None,
    tage: int = 30,
    adapter: "_anbieter.OpenRouter | None" = None,
    schwellen: "_anbieter.Schwellen | None" = None,
) -> list[str]:
    """Die Zeilen des Bausteins (ohne Streamlit, testbar).

    Mit Key: Verbrauch von Limit mit Ampel, dann heute/Monat. Ohne Key oder bei Fehler: lokale Schätzung
    der letzten `tage` Tage aus der Kosten-DB (nur `tag`, falls gesetzt)."""
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
            zeilen.append(f"heute {betrag(status.heute)} · Monat {betrag(status.monat)}")
            return zeilen
        zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} Anbieter: {status.fehler}")
    else:
        zeilen.append(f"{AMPEL_ZEICHEN[_anbieter.GRAU]} kein Key ({key_env})")
    lokal = cost_tracker.total_cost(days=tage, tag=tag)
    zeilen.append(f"lokal {tage} Tage: {betrag(lokal)} (Schätzung)")
    return zeilen


def kosten_seitenleiste(
    key_env: str = "OPENROUTER_API_KEY",
    tag: str | None = None,
    titel: str | None = "Kosten",
    tage: int = 30,
    adapter: "_anbieter.OpenRouter | None" = None,
    schwellen: "_anbieter.Schwellen | None" = None,
) -> list[str]:
    """Schreibt die Kostenzeilen klein in die Streamlit-Seitenleiste und gibt sie zurück."""
    import streamlit as st  # optionale Abhängigkeit, nur hier

    zeilen = kosten_zeilen(key_env, tag=tag, tage=tage, adapter=adapter, schwellen=schwellen)
    with st.sidebar:
        # "$" würde Streamlit als Formel lesen
        text = "  \n".join(zeilen).replace("$", "\\$")
        st.caption(f"**{titel}**  \n{text}" if titel else text)
    return zeilen
