"""Logik der Kosten-App ohne Web-Framework: Werte der Übersicht, Hinweise, Umrechnung € ↔ $, Eingaben, Pläne.

Alle Beträge kommen in USD vom Anbieter bzw. aus der Kosten-DB; angezeigt wird nur Euro (`eur`). Ohne Kurs
erscheint „–“, nie ein Dollar-Betrag.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from llm_core import anbieter as an
from llm_core.kosten_app.kurs import Kurs
from llm_core.ui import MONATSNAMEN, betrag

OPENROUTER_KONSOLE = "https://openrouter.ai/activity"
ANTHROPIC_KONSOLE = "https://platform.claude.com/usage"

LIMIT_MAX_EUR = 100.0      # Obergrenze je Limit (Nutzer, 2026-10-29)
NAME_MAX = 60
NAME_MUSTER = re.compile(r"^[A-Za-z0-9ÄÖÜäöüß][A-Za-z0-9ÄÖÜäöüß _.\-]*$")

# Zurücksetzen des Limits: Wert für OpenRouter -> Anzeige (Reihenfolge = Auswahl)
RESETS = {an.NIE: "nie", "daily": "täglich", "weekly": "wöchentlich", "monthly": "monatlich"}

GESPERRT = "aus"     # Ampel eines gesperrten Keys (grau, kein Problem)


class Eingabefehler(ValueError):
    """Ungültige Eingabe; die Meldung ist für die Anzeige gedacht."""


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

def eur(usd: float | None, kurs: Kurs | None) -> str:
    """USD-Betrag als Euro ('3,86 €'); ohne Kurs oder Wert '–'."""
    if usd is None or kurs is None:
        return "–"
    return betrag(usd * kurs.wert, "€")


def prozent(anteil: float | None) -> str:
    return "" if anteil is None else f"{round(anteil * 100)} %"


def reset_text(limit_reset: str | None) -> str:
    return RESETS.get(limit_reset or an.NIE, limit_reset or "nie")


def limit_text(status: an.KeyStatus, kurs: Kurs | None) -> str:
    """'10,00 € monatlich', 'ohne Limit'."""
    if status.limit is None:
        return "ohne Limit"
    zusatz = "" if (status.limit_reset or an.NIE) == an.NIE else f" {reset_text(status.limit_reset)}"
    return f"{eur(status.limit, kurs)}{zusatz}"


def limit_vorschlag(status: an.KeyStatus, kurs: Kurs | None) -> str:
    """Startwert des Eingabefelds: heutiges Limit in Euro ('9,00'), sonst leer."""
    if status.limit is None or kurs is None:
        return ""
    return f"{status.limit * kurs.wert:.2f}".replace(".", ",")


def kurs_text(kurs: Kurs | None) -> str:
    """'1 USD = 0,90 € (EZB, 28.10.)' — „USD“ statt „$“: die App zeigt nirgends ein Dollarzeichen."""
    if kurs is None:
        return "kein Euro-Kurs"
    wert = f"{kurs.wert:.4f}".rstrip("0")
    wert = (wert + "0" * (2 - len(wert.split(".")[1]))).replace(".", ",")    # mindestens zwei Stellen
    if kurs.quelle == "fest":
        herkunft = "fest"
    else:
        herkunft = ", ".join(t for t in ("EZB", kurs.datum_kurz(), "alt" if kurs.alt else "") if t)
    return f"1 USD = {wert} € ({herkunft})"


# ---------------------------------------------------------------------------
# Umrechnung und Eingaben
# ---------------------------------------------------------------------------

def eur_zu_usd(eur_betrag: float, kurs: Kurs) -> float:
    """Euro-Eingabe in USD zum Tageskurs, auf Cent gerundet."""
    return round(eur_betrag / kurs.wert + 1e-9, 2)


def lies_limit(text: str | None) -> float:
    """'12,50' / '12.5' / '1.000' -> Euro; 0 bis LIMIT_MAX_EUR."""
    roh = (text or "").strip().replace("€", "").replace(" ", "").replace(" ", "")
    if not roh:
        raise Eingabefehler("Bitte ein Limit in Euro eingeben.")
    if "," in roh:
        roh = roh.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", roh):
        roh = roh.replace(".", "")
    try:
        wert = float(roh)
    except ValueError:
        raise Eingabefehler("Das Limit ist keine Zahl.") from None
    if not math.isfinite(wert) or wert < 0:
        raise Eingabefehler("Das Limit muss 0 oder größer sein.")
    if wert > LIMIT_MAX_EUR:
        raise Eingabefehler(f"Höchstens {betrag(LIMIT_MAX_EUR, '€')}.")
    return round(wert, 2)


def lies_reset(text: str | None) -> str:
    wert = (text or an.NIE).strip()
    if wert not in RESETS:
        raise Eingabefehler("Unbekannter Zeitraum für das Zurücksetzen.")
    return wert


def lies_name(text: str | None) -> str:
    name = " ".join((text or "").split())
    if not name:
        raise Eingabefehler("Bitte einen Namen eingeben.")
    if len(name) > NAME_MAX:
        raise Eingabefehler(f"Der Name ist zu lang (höchstens {NAME_MAX} Zeichen).")
    if not NAME_MUSTER.match(name):
        raise Eingabefehler("Erlaubt sind Buchstaben, Ziffern, Leerzeichen, Punkt, Bindestrich und Unterstrich.")
    return name


# ---------------------------------------------------------------------------
# Übersicht
# ---------------------------------------------------------------------------

@dataclass
class Hinweis:
    stufe: str          # "rot", "orange", "grau"
    text: str


@dataclass
class KeyZeile:
    schluessel: str     # in der URL: OpenRouter-Kennung (hash) bzw. "env-<Variable>", nie der Key
    status: an.KeyStatus
    ampel: str          # an.GRUEN/GELB/ROT/GRAU oder GESPERRT

    @property
    def name(self) -> str:
        return self.status.name

    @property
    def gesperrt(self) -> bool:
        return self.status.aktiv is False

    @property
    def verbrauch(self) -> float | None:
        return self.status.verbrauch_im_limit

    @property
    def anteil(self) -> float | None:
        return an.anteil(self.status)


@dataclass
class Karte:
    titel: str
    link: str
    zeilen: list[tuple[str, float | None]]
    fehler: str | None = None


@dataclass
class Uebersicht:
    openrouter: Karte
    anthropic: Karte
    keys: list[KeyZeile]
    hinweise: list[Hinweis]
    kurs: Kurs | None
    stand: datetime
    verwaltung: bool                 # Provisioning-Key vorhanden
    schwellen: an.Schwellen = field(default_factory=an.Schwellen)


def schluessel(status: an.KeyStatus) -> str:
    return status.id or f"env-{status.name}"


def ist_anbieter_fehler(status: an.KeyStatus) -> bool:
    """Eintrag ohne Key, nur mit Fehler (z. B. „Konto: 401“ aus alle_keys)."""
    return bool(status.fehler) and not status.kennung and status.id is None


def key_ampel(status: an.KeyStatus, schwellen: an.Schwellen) -> str:
    return GESPERRT if status.aktiv is False else an.ampel(status, schwellen)


_RANG = {an.ROT: 0, an.GELB: 1, an.GRAU: 2, an.GRUEN: 3, GESPERRT: 4}


def key_zeilen(keys: list[an.KeyStatus], schwellen: an.Schwellen) -> list[KeyZeile]:
    """Eine Zeile je Key; Probleme zuerst (rot, orange, Fehler), dann nach Verbrauch, gesperrte zuletzt."""
    zeilen = [KeyZeile(schluessel(s), s, key_ampel(s, schwellen)) for s in keys if not ist_anbieter_fehler(s)]
    return sorted(zeilen, key=lambda z: (_RANG[z.ampel], -(z.verbrauch or 0.0), z.name.lower()))


def hinweise(konto: an.KontoStatus | None, keys: list[an.KeyStatus], kurs: Kurs | None,
             anthropic_fehler: str | None, schwellen: an.Schwellen) -> list[Hinweis]:
    """Nur was Aufmerksamkeit braucht; alles in Ordnung -> []."""
    aktiv = [s for s in keys if s.aktiv is not False and not s.fehler]
    ohne_limit = [s for s in aktiv if s.limit is None and s.limit_abfragbar]
    erreicht = [s for s in aktiv if s.limit is not None and an.ampel(s, schwellen) == an.ROT]
    fast = [s for s in aktiv if an.ampel(s, schwellen) == an.GELB]
    liste: list[Hinweis] = []
    for stufe, text, gruppe in (("rot", "Ohne Limit", ohne_limit), ("rot", "Limit erreicht", erreicht),
                                ("orange", "Limit fast erreicht", fast)):
        if gruppe:
            liste.append(Hinweis(stufe, f"{text}: " + ", ".join(s.name for s in gruppe)))
    fehler = [s.fehler for s in keys if ist_anbieter_fehler(s)]
    if konto is not None and konto.fehler:
        fehler.append(konto.fehler)
    if fehler:
        liste.append(Hinweis("grau", f"OpenRouter nicht erreichbar: {fehler[0]}"))
    key_fehler = [s for s in keys if s.fehler and not ist_anbieter_fehler(s)]
    if key_fehler:
        liste.append(Hinweis("grau", "Abfrage fehlgeschlagen: "
                             + ", ".join(f"{s.name} ({s.fehler})" for s in key_fehler)))
    if anthropic_fehler:
        liste.append(Hinweis("grau", f"Anthropic: {anthropic_fehler}"))
    if kurs is None:
        liste.append(Hinweis("grau", "Kein Euro-Kurs (EZB nicht erreichbar) — Beträge fehlen, bis ein Kurs da ist."))
    return liste


def openrouter_karte(konto: an.KontoStatus | None, keys: list[an.KeyStatus], monat_nr: int) -> Karte:
    heute = [s.heute for s in keys if s.heute is not None]
    monat = [s.monat for s in keys if s.monat is not None]
    zeilen = [("heute", sum(heute) if heute else None), (MONATSNAMEN[monat_nr - 1], sum(monat) if monat else None)]
    if konto is not None and not konto.fehler:
        zeilen.append(("Guthaben", konto.rest))
    return Karte("OpenRouter", OPENROUTER_KONSOLE, zeilen)


def anthropic_karte(heute: float | None, monat: float | None, monat_nr: int) -> Karte:
    return Karte("Anthropic", ANTHROPIC_KONSOLE, [("heute", heute), (MONATSNAMEN[monat_nr - 1], monat)])


def baue_uebersicht(konto: an.KontoStatus | None, keys: list[an.KeyStatus],
                    anthropic: tuple[float | None, float | None, str | None], kurs: Kurs | None,
                    jetzt: datetime, verwaltung: bool, schwellen: an.Schwellen | None = None) -> Uebersicht:
    """Alle Werte der Startseite aus den Antworten der Anbieter und der Kosten-DB (rein, ohne Netz)."""
    s = schwellen or an.Schwellen.from_env()
    a_heute, a_monat, a_fehler = anthropic
    return Uebersicht(
        openrouter=openrouter_karte(konto, keys, jetzt.month),
        anthropic=anthropic_karte(a_heute, a_monat, jetzt.month),
        keys=key_zeilen(keys, s),
        hinweise=hinweise(konto, keys, kurs, a_fehler, s),
        kurs=kurs, stand=jetzt, verwaltung=verwaltung, schwellen=s,
    )


# ---------------------------------------------------------------------------
# Pläne (Bestätigung vor jeder Änderung)
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    """Eine geplante Änderung; ausgeführt wird erst nach der Bestätigung (web.bestaetigen)."""
    aktion: str                 # "limit", "sperren", "entsperren", "anlegen"
    titel: str
    vorher: str
    nachher: str
    knopf: str
    key_id: str | None = None
    name: str | None = None
    limit_usd: float | None = None
    reset: str | None = None


def plan_limit(status: an.KeyStatus, limit_eur: float, reset: str, kurs: Kurs) -> Plan:
    usd = eur_zu_usd(limit_eur, kurs)
    neu = an.KeyStatus(status.anbieter, status.name, limit=usd, limit_reset=None if reset == an.NIE else reset)
    return Plan("limit", f"Limit von {status.name} ändern", limit_text(status, kurs), limit_text(neu, kurs),
                "Limit ändern", key_id=status.id, limit_usd=usd, reset=reset)


def plan_sperren(status: an.KeyStatus) -> Plan:
    sperren = status.aktiv is not False
    return Plan("sperren" if sperren else "entsperren", f"{status.name} {'sperren' if sperren else 'entsperren'}",
                "aktiv" if sperren else "gesperrt", "gesperrt" if sperren else "aktiv",
                "Sperren" if sperren else "Entsperren", key_id=status.id)


def plan_anlegen(name: str, limit_eur: float, reset: str, kurs: Kurs) -> Plan:
    usd = eur_zu_usd(limit_eur, kurs)
    neu = an.KeyStatus("openrouter", name, limit=usd, limit_reset=None if reset == an.NIE else reset)
    return Plan("anlegen", "Neuen Key anlegen", "–", f"{name} · Limit {limit_text(neu, kurs)}", "Key anlegen",
                name=name, limit_usd=usd, reset=reset)


def ausfuehren(plan: Plan, openrouter: an.OpenRouter) -> an.Aenderung:
    """Führt einen bestätigten Plan aus (nur aus web.bestaetigen aufrufen)."""
    if plan.aktion == "limit":
        return openrouter.limit_aendern(plan.key_id, plan.limit_usd, limit_reset=plan.reset, bestaetigt=True)
    if plan.aktion in ("sperren", "entsperren"):
        return openrouter.key_aktivieren(plan.key_id, plan.aktion == "entsperren", bestaetigt=True)
    if plan.aktion == "anlegen":
        reset = None if plan.reset == an.NIE else plan.reset
        return openrouter.key_anlegen(plan.name, plan.limit_usd, limit_reset=reset, bestaetigt=True)
    raise ValueError(f"Unbekannte Aktion: {plan.aktion}")
