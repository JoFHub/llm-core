"""Euro-Kurs: Euro je US-Dollar, ungefähr (EZB-Referenzkurs über frankfurter.dev, ohne Key).

- Höchstens zweimal am Tag abfragen (nach einem Treffer 12 h nicht neu), im Prozess zwischenspeichern.
- Ohne Antwort (Netz, Dienst weg) gilt der letzte bekannte Kurs; ohne jeden Kurs None — die App zeigt dann
  keine Beträge, nie Dollar als Ersatz. Nach einem Fehlschlag frühestens nach einer Stunde neu versuchen.
- `LLM_CORE_EUR_KURS` setzt einen festen Kurs (z. B. 0,92), ohne Abfrage.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable

EUR_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR"
GUELTIG = 12 * 3600          # nach einem Treffer so lange nicht neu fragen
NEUER_VERSUCH = 3600         # nach einem Fehlschlag
FEST_ENV = "LLM_CORE_EUR_KURS"


@dataclass(frozen=True)
class Kurs:
    wert: float                  # Euro je Dollar
    datum: str | None = None     # Tag des Referenzkurses (JJJJ-MM-TT), None beim festen Kurs
    quelle: str = "EZB"          # "EZB" oder "fest"
    alt: bool = False            # letzter bekannter Kurs, die neue Abfrage schlug fehl

    def datum_kurz(self) -> str:
        """'2026-10-28' -> '28.10.'"""
        if not self.datum or len(self.datum) < 10:
            return ""
        return f"{self.datum[8:10]}.{self.datum[5:7]}."


def abfragen(timeout: float = 5.0) -> Kurs | None:
    """Kurs bei frankfurter.dev; None bei jedem Fehler."""
    try:
        with urllib.request.urlopen(EUR_URL, timeout=timeout) as antwort:
            daten = json.load(antwort)
        wert = float(daten["rates"]["EUR"])
    except Exception:
        return None
    if not 0.2 < wert < 5:      # offensichtlich falsch: lieber kein Kurs
        return None
    return Kurs(wert, datum=str(daten.get("date") or "") or None)


def fester_kurs() -> Kurs | None:
    text = os.environ.get(FEST_ENV, "").strip().replace(",", ".")
    if not text:
        return None
    try:
        wert = float(text)
    except ValueError:
        return None
    return Kurs(wert, quelle="fest") if wert > 0 else None


class KursQuelle:
    """Kurs mit Zwischenspeicher im Prozess. abrufen und uhr sind für Tests austauschbar."""

    def __init__(self, abrufen: Callable[[], Kurs | None] = abfragen,
                 uhr: Callable[[], float] = time.monotonic) -> None:
        self._abrufen = abrufen
        self._uhr = uhr
        self._sperre = threading.Lock()
        self._kurs: Kurs | None = None
        self._naechster = 0.0      # frühester Zeitpunkt der nächsten Abfrage

    def kurs(self) -> Kurs | None:
        fest = fester_kurs()
        if fest:
            return fest
        with self._sperre:
            jetzt = self._uhr()
            if jetzt >= self._naechster:
                neu = self._abrufen()
                if neu:
                    self._kurs, self._naechster = neu, jetzt + GUELTIG
                else:
                    self._naechster = jetzt + NEUER_VERSUCH
                    if self._kurs and not self._kurs.alt:
                        self._kurs = Kurs(self._kurs.wert, self._kurs.datum, self._kurs.quelle, alt=True)
            return self._kurs

    def leeren(self) -> None:
        """Nächste Anfrage fragt neu (der letzte Kurs bleibt als Ersatz)."""
        with self._sperre:
            self._naechster = 0.0
