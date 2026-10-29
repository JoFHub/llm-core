"""
Anbieter-Status — echter Verbrauch, Limits und Keys direkt beim Anbieter, ohne Oberfläche.

Einheitliches Ergebnis je Key (KeyStatus) und je Konto (KontoStatus), dazu die Ampel.
Adapter: OpenRouter (eigener Key, mit Provisioning-/Management-Key alle Keys, Guthaben und
Verwaltung) und Anthropic (API-Keys gekürzt; mit Admin-Key auch Kosten der Organisation).

Grundsätze:
- Ein Key verlässt nie den Prozess über Rückgabewerte, Fehlermeldungen oder Logs; angezeigt wird nur
  die gekürzte Kennung (kuerze_key). Einzige Ausnahme: der Klartext eines neu angelegten Keys
  (Aenderung.klartext), genau für die einmalige Anzeige.
- Abfragen werfen nicht: Fehler (401, 402, Timeout, Netz) stehen im Feld `fehler`.
- Kurzer Zwischenspeicher (Standard 60 s) je Anfrage und Key; Änderungen leeren ihn.
- Verwaltungsaktionen laufen nur mit bestaetigt=True (sonst BestaetigungFehlt).
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

# Transport: (methode, url, header, json_body, timeout) -> (http_status, json_antwort).
# Netzfehler/Timeout werfen (OSError/TimeoutError); Tests ersetzen ihn durch Fixtures.
Transport = Callable[[str, str, dict, "dict | None", float], "tuple[int, Any]"]

OPENROUTER_BASIS = "https://openrouter.ai/api/v1"
ANTHROPIC_BASIS = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

PROVISIONING_ENV = ("OPENROUTER_PROVISIONING_KEY", "OPENROUTER_MANAGEMENT_KEY")
ANTHROPIC_ADMIN_ENV = "ANTHROPIC_ADMIN_KEY"
KEYRING_DIENST = "llm-core"

# limit_reset für „Limit gilt insgesamt“ (OpenRouter: null); None heißt in limit_aendern „unverändert“
NIE = "never"

STANDARD_TIMEOUT = 10.0
STANDARD_CACHE_TTL = 60.0


# ---------------------------------------------------------------------------
# Ergebnisse
# ---------------------------------------------------------------------------

@dataclass
class KeyStatus:
    """Stand eines Keys beim Anbieter. Beträge in USD; None = unbekannt bzw. nicht geliefert."""
    anbieter: str
    name: str
    kennung: str = ""                       # gekürzt, nie der Key
    verbrauch_gesamt: float | None = None
    heute: float | None = None
    monat: float | None = None
    limit: float | None = None              # None = ohne Limit (bzw. unbekannt, siehe limit_abfragbar)
    rest: float | None = None
    limit_reset: str | None = None          # z. B. "daily"/"monthly"; None = Limit gilt für immer
    aktiv: bool | None = None
    id: str | None = None                   # Verwaltungs-Kennung (OpenRouter: hash)
    limit_abfragbar: bool = True            # False: Anbieter liefert kein Limit (Ampel grau statt rot)
    stand: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fehler: str | None = None

    @property
    def verbrauch_im_limit(self) -> float | None:
        """Verbrauch, der gegen das Limit zählt. Bei Limits mit Zurücksetzen (täglich/monatlich) ist das
        limit - rest, nicht der Gesamtverbrauch."""
        if self.limit is not None and self.rest is not None:
            return max(self.limit - self.rest, 0.0)
        return self.verbrauch_gesamt


@dataclass
class KontoStatus:
    """Stand des Kontos (Guthaben). Beträge in USD."""
    anbieter: str
    guthaben: float | None = None           # eingezahlt gesamt
    verbraucht: float | None = None
    heute: float | None = None
    monat: float | None = None
    stand: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fehler: str | None = None

    @property
    def rest(self) -> float | None:
        if self.guthaben is None or self.verbraucht is None:
            return None
        return self.guthaben - self.verbraucht


@dataclass
class Aenderung:
    """Ergebnis einer Verwaltungsaktion: vorher/nachher; klartext nur beim Anlegen eines Keys."""
    aktion: str
    ok: bool
    vorher: KeyStatus | None = None
    nachher: KeyStatus | None = None
    fehler: str | None = None
    klartext: str | None = field(default=None, repr=False)


class BestaetigungFehlt(RuntimeError):
    """Verwaltungsaktion ohne bestaetigt=True aufgerufen."""


# ---------------------------------------------------------------------------
# Ampel
# ---------------------------------------------------------------------------

GRUEN, GELB, ROT, GRAU = "gruen", "gelb", "rot", "grau"


@dataclass(frozen=True)
class Schwellen:
    gelb: float = 0.8    # ab diesem Anteil des Limits gelb
    rot: float = 1.0     # ab diesem Anteil rot

    @classmethod
    def from_env(cls) -> "Schwellen":
        """LLM_CORE_AMPEL_GELB / LLM_CORE_AMPEL_ROT (Anteile, z. B. 0.8 und 1.0)."""
        def _f(name: str, standard: float) -> float:
            try:
                return float(os.environ.get(name, standard))
            except ValueError:
                return standard
        return cls(gelb=_f("LLM_CORE_AMPEL_GELB", 0.8), rot=_f("LLM_CORE_AMPEL_ROT", 1.0))


def ampel(status: KeyStatus, schwellen: Schwellen | None = None) -> str:
    """grün < gelb-Schwelle ≤ gelb < rot-Schwelle ≤ rot. Key ohne Limit = rot (Risiko).
    Fehler oder vom Anbieter nicht abfragbares Limit = grau."""
    s = schwellen or Schwellen.from_env()
    if status.fehler:
        return GRAU
    if status.limit is None:
        return ROT if status.limit_abfragbar else GRAU
    verbraucht = status.verbrauch_im_limit or 0.0
    if status.limit <= 0:
        return ROT
    anteil = verbraucht / status.limit
    if anteil >= s.rot:
        return ROT
    if anteil >= s.gelb:
        return GELB
    return GRUEN


def anteil(status: KeyStatus) -> float | None:
    """Verbrauchter Anteil des Limits (0.41 = 41 %), None ohne Limit."""
    if status.limit is None or status.limit <= 0:
        return None
    return (status.verbrauch_im_limit or 0.0) / status.limit


# ---------------------------------------------------------------------------
# Keys: Kürzen, Umgebung, Provisioning-Key
# ---------------------------------------------------------------------------

def kuerze_key(key: str | None) -> str:
    """'sk-or-v1-0123…cdef' -> 'sk-or-v1-…cdef'. Zeigt nie mehr als Präfix und letzte 4 Zeichen."""
    if not key:
        return ""
    key = key.strip()
    if len(key) <= 8:
        return "…" + key[-2:]
    teile = key.split("-")
    praefix = "-".join(teile[:-1]) + "-" if len(teile) > 1 and len(teile[-1]) > 8 else ""
    if len(praefix) > 12:
        praefix = ""
    return f"{praefix}…{key[-4:]}"


def keys_aus_umgebung(praefix: str = "OPENROUTER_API_KEY") -> dict[str, str]:
    """Alle gesetzten Variablen OPENROUTER_API_KEY* (Name -> Key), sortiert."""
    return {n: v.strip() for n, v in sorted(os.environ.items()) if n.startswith(praefix) and v.strip()}


def _aus_keyring(name: str) -> str | None:
    try:
        import keyring  # optional (Windows-Anmeldeinformationsspeicher)
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_DIENST, name) or None
    except Exception:
        return None


def provisioning_key() -> str | None:
    """Provisioning-/Management-Key aus der Umgebung (.env), sonst aus keyring (Dienst 'llm-core')."""
    for name in PROVISIONING_ENV:
        wert = os.environ.get(name, "").strip()
        if wert:
            return wert
    return _aus_keyring(PROVISIONING_ENV[0])


def anthropic_admin_key() -> str | None:
    wert = os.environ.get(ANTHROPIC_ADMIN_ENV, "").strip()
    return wert or _aus_keyring(ANTHROPIC_ADMIN_ENV)


# ---------------------------------------------------------------------------
# HTTP, Fehler, Zwischenspeicher
# ---------------------------------------------------------------------------

class AnbieterFehler(Exception):
    """Interner Fehler mit bereinigter Meldung (ohne Key)."""


def urllib_transport(methode: str, url: str, header: dict, body: dict | None, timeout: float) -> tuple[int, Any]:
    daten = json.dumps(body).encode() if body is not None else None
    anfrage = urllib.request.Request(url, data=daten, method=methode, headers=dict(header))
    if daten is not None:
        anfrage.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
            roh = antwort.read()
            return antwort.status, (json.loads(roh) if roh else None)
    except urllib.error.HTTPError as e:
        try:
            roh = e.read()
            return e.code, (json.loads(roh) if roh else None)
        except Exception:
            return e.code, None


_FEHLERTEXTE = {
    400: "Anfrage abgelehnt (400)",
    401: "Key ungültig oder widerrufen (401)",
    402: "Guthaben oder Limit erschöpft (402)",
    403: "Keine Berechtigung (403) — für diese Abfrage ist ein Provisioning-/Admin-Key nötig",
    404: "Nicht gefunden (404)",
    429: "Zu viele Anfragen (429)",
}


def _fehlertext(status: int, antwort: Any) -> str:
    text = _FEHLERTEXTE.get(status) or (f"Anbieter-Fehler ({status})" if status >= 500 else f"HTTP {status}")
    detail = ""
    if isinstance(antwort, dict):
        err = antwort.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
        elif isinstance(err, str):
            detail = err
    return f"{text}: {detail[:200]}" if detail else text


def _bereinige(text: str, *geheim: str | None) -> str:
    for g in geheim:
        if g:
            text = text.replace(g, kuerze_key(g))
    return text


def _fingerabdruck(key: str | None) -> str:
    return hashlib.sha256((key or "").encode()).hexdigest()[:16]


class _Zwischenspeicher:
    def __init__(self) -> None:
        self._daten: dict[tuple, tuple[float, Any]] = {}
        self._sperre = threading.Lock()

    def hole(self, schluessel: tuple, ttl: float) -> tuple[bool, Any]:
        with self._sperre:
            eintrag = self._daten.get(schluessel)
        if eintrag and time.monotonic() - eintrag[0] < ttl:
            return True, eintrag[1]
        return False, None

    def setze(self, schluessel: tuple, wert: Any) -> None:
        with self._sperre:
            self._daten[schluessel] = (time.monotonic(), wert)

    def leeren(self) -> None:
        with self._sperre:
            self._daten.clear()


_zwischenspeicher = _Zwischenspeicher()


def cache_leeren() -> None:
    """Leert den Zwischenspeicher aller Adapter (z. B. Knopf „Aktualisieren“)."""
    _zwischenspeicher.leeren()


class _Client:
    """Gemeinsame HTTP-Hülle: Timeout, Fehler bereinigen, GET zwischenspeichern."""

    def __init__(self, transport: Transport | None, timeout: float, cache_ttl: float) -> None:
        self.transport = transport or urllib_transport
        self.timeout = timeout
        self.cache_ttl = cache_ttl

    def anfrage(self, methode: str, url: str, header: dict, key: str, body: dict | None = None) -> Any:
        schluessel = (methode, url, _fingerabdruck(key))
        if methode == "GET" and self.cache_ttl > 0:
            treffer, wert = _zwischenspeicher.hole(schluessel, self.cache_ttl)
            if treffer:
                return wert
        try:
            status, antwort = self.transport(methode, url, header, body, self.timeout)
        except (TimeoutError, socket.timeout):
            raise AnbieterFehler(f"Zeitüberschreitung nach {self.timeout:g} s") from None
        except OSError as e:
            grund = getattr(e, "reason", None) or e
            raise AnbieterFehler(_bereinige(f"Netzwerkfehler: {grund}", key)) from None
        except Exception as e:  # Transport-Fehler aller Art, nie mit Key
            raise AnbieterFehler(_bereinige(f"Fehler: {type(e).__name__}: {e}", key)) from None
        if status >= 400:
            raise AnbieterFehler(_bereinige(_fehlertext(status, antwort), key))
        if methode == "GET" and self.cache_ttl > 0:
            _zwischenspeicher.setze(schluessel, antwort)
        else:
            _zwischenspeicher.leeren()   # Änderung: gespeicherte Stände sind veraltet
        return antwort


# ---------------------------------------------------------------------------
# Adapter-Schnittstelle
# ---------------------------------------------------------------------------

class Adapter(Protocol):
    anbieter: str

    def eingerichtet(self) -> bool: ...
    def keys(self) -> list[KeyStatus]: ...
    def konto(self) -> KontoStatus | None: ...


def _zahl(wert: Any) -> float | None:
    try:
        return None if wert is None else float(wert)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

class OpenRouter:
    """OpenRouter-Adapter.

    Endpunkte (Doku openrouter.ai/docs, „API Keys“ und „Credits“):
      GET   /key            Stand des aufrufenden Keys
      GET   /keys           alle Keys des Kontos (Management-/Provisioning-Key), Seiten über offset
      POST  /keys           Key anlegen {name, limit, limit_reset?} -> {data, key}
      PATCH /keys/{hash}    {name?, disabled?, limit?, limit_reset?}
      GET   /credits        Guthaben {total_credits, total_usage} (Management-Key)
    """
    anbieter = "openrouter"

    def __init__(
        self,
        api_keys: dict[str, str] | None = None,
        provisioning: str | None = None,
        transport: Transport | None = None,
        timeout: float = STANDARD_TIMEOUT,
        cache_ttl: float = STANDARD_CACHE_TTL,
        basis_url: str = OPENROUTER_BASIS,
    ) -> None:
        self.api_keys = keys_aus_umgebung() if api_keys is None else dict(api_keys)
        self._provisioning = provisioning_key() if provisioning is None else (provisioning or None)
        self._client = _Client(transport, timeout, cache_ttl)
        self.basis_url = basis_url.rstrip("/")

    def __repr__(self) -> str:  # nie Keys zeigen
        return f"OpenRouter(keys={list(self.api_keys)}, provisioning={'ja' if self._provisioning else 'nein'})"

    @property
    def hat_provisioning(self) -> bool:
        return bool(self._provisioning)

    def eingerichtet(self) -> bool:
        return bool(self._provisioning or self.api_keys)

    def _header(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    def _anfrage(self, methode: str, pfad: str, key: str, body: dict | None = None) -> Any:
        return self._client.anfrage(methode, self.basis_url + pfad, self._header(key), key, body)

    # -- Umwandlung -------------------------------------------------------

    def _status(self, d: dict, name: str | None = None, kennung: str | None = None) -> KeyStatus:
        disabled = d.get("disabled")
        return KeyStatus(
            anbieter=self.anbieter,
            name=name or str(d.get("name") or d.get("label") or "?"),
            kennung=kennung if kennung is not None else str(d.get("label") or ""),
            verbrauch_gesamt=_zahl(d.get("usage")),
            heute=_zahl(d.get("usage_daily")),
            monat=_zahl(d.get("usage_monthly")),
            limit=_zahl(d.get("limit")),
            rest=_zahl(d.get("limit_remaining")),
            limit_reset=d.get("limit_reset") or None,
            aktiv=None if disabled is None else not bool(disabled),
            id=d.get("hash"),
        )

    # -- Abfragen (werfen nicht) -----------------------------------------

    def key_status(self, api_key: str, name: str = "") -> KeyStatus:
        """Stand eines einzelnen Keys über GET /key (mit dem Key selbst)."""
        kennung = kuerze_key(api_key)
        try:
            antwort = self._anfrage("GET", "/key", api_key)
            daten = (antwort or {}).get("data") or {}
            return self._status(daten, name=name or str(daten.get("label") or kennung), kennung=kennung)
        except AnbieterFehler as e:
            return KeyStatus(self.anbieter, name or kennung, kennung=kennung, fehler=str(e))

    def umgebungs_keys(self) -> list[KeyStatus]:
        return [self.key_status(wert, name) for name, wert in self.api_keys.items()]

    def alle_keys(self, mit_deaktivierten: bool = True) -> list[KeyStatus]:
        """Alle Keys des Kontos (Provisioning-Key nötig). Bei Fehler ein Eintrag mit `fehler`."""
        if not self._provisioning:
            return [KeyStatus(self.anbieter, "Konto", fehler="Kein Provisioning-Key eingerichtet")]
        ergebnis: list[KeyStatus] = []
        offset = 0
        try:
            while True:
                query = {"offset": offset}
                if mit_deaktivierten:
                    query["include_disabled"] = "true"
                antwort = self._anfrage("GET", "/keys?" + urllib.parse.urlencode(query), self._provisioning)
                seite = (antwort or {}).get("data") or []
                ergebnis += [self._status(d) for d in seite]
                if len(seite) < 100 or offset > 10_000:
                    break
                offset += len(seite)
        except AnbieterFehler as e:
            return ergebnis + [KeyStatus(self.anbieter, "Konto", fehler=str(e))]
        return ergebnis

    def keys(self) -> list[KeyStatus]:
        """Mit Provisioning-Key alle Keys des Kontos, sonst die Keys aus der Umgebung."""
        return self.alle_keys() if self._provisioning else self.umgebungs_keys()

    def konto(self) -> KontoStatus | None:
        """Guthaben des Kontos über GET /credits (Provisioning-Key); ohne ihn None."""
        if not self._provisioning:
            return None
        try:
            daten = ((self._anfrage("GET", "/credits", self._provisioning)) or {}).get("data") or {}
            return KontoStatus(self.anbieter, guthaben=_zahl(daten.get("total_credits")),
                               verbraucht=_zahl(daten.get("total_usage")))
        except AnbieterFehler as e:
            return KontoStatus(self.anbieter, fehler=str(e))

    # -- Verwaltung (nur mit Provisioning-Key und Bestätigung) ------------

    def _verwaltung_pruefen(self, bestaetigt: bool) -> None:
        if not bestaetigt:
            raise BestaetigungFehlt("Verwaltungsaktion nur mit bestaetigt=True")

    def _finde(self, key_id: str) -> KeyStatus | None:
        for s in self.alle_keys():
            if s.id == key_id:
                return s
        return None

    def _patch(self, aktion: str, key_id: str, body: dict, bestaetigt: bool) -> Aenderung:
        self._verwaltung_pruefen(bestaetigt)
        if not self._provisioning:
            return Aenderung(aktion, False, fehler="Kein Provisioning-Key eingerichtet")
        vorher = self._finde(key_id)
        try:
            pfad = "/keys/" + urllib.parse.quote(key_id, safe="")
            antwort = self._anfrage("PATCH", pfad, self._provisioning, body)
        except AnbieterFehler as e:
            return Aenderung(aktion, False, vorher=vorher, fehler=str(e))
        daten = (antwort or {}).get("data") or {}
        return Aenderung(aktion, True, vorher=vorher, nachher=self._status(daten) if daten else None)

    def limit_aendern(self, key_id: str, limit: float | None, *, bestaetigt: bool = False,
                      limit_reset: str | None = None) -> Aenderung:
        """Limit setzen (USD); None entfernt das Limit. limit_reset: "daily"/"weekly"/"monthly", NIE (Limit gilt
        insgesamt, entfernt ein Zurücksetzen) oder None (unverändert)."""
        body: dict[str, Any] = {"limit": limit}
        if limit_reset is not None:
            body["limit_reset"] = None if limit_reset == NIE else limit_reset
        return self._patch("limit", key_id, body, bestaetigt)

    def key_aktivieren(self, key_id: str, aktiv: bool, *, bestaetigt: bool = False) -> Aenderung:
        return self._patch("aktivieren" if aktiv else "deaktivieren", key_id, {"disabled": not aktiv}, bestaetigt)

    def key_anlegen(self, name: str, limit: float | None, *, bestaetigt: bool = False,
                    limit_reset: str | None = None) -> Aenderung:
        """Neuen Key anlegen. Der Klartext steht nur in Aenderung.klartext — einmal anzeigen, nie speichern."""
        self._verwaltung_pruefen(bestaetigt)
        if not self._provisioning:
            return Aenderung("anlegen", False, fehler="Kein Provisioning-Key eingerichtet")
        if not name.strip():
            return Aenderung("anlegen", False, fehler="Name fehlt")
        body: dict[str, Any] = {"name": name.strip(), "limit": limit}
        if limit_reset is not None:
            body["limit_reset"] = limit_reset
        try:
            antwort = self._anfrage("POST", "/keys", self._provisioning, body) or {}
        except AnbieterFehler as e:
            return Aenderung("anlegen", False, fehler=str(e))
        klartext = antwort.get("key")
        daten = antwort.get("data") or {}
        nachher = self._status(daten) if daten else None
        if nachher and klartext and not nachher.kennung:
            nachher = replace(nachher, kennung=kuerze_key(klartext))
        return Aenderung("anlegen", True, nachher=nachher, klartext=klartext)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class Anthropic:
    """Anthropic-Adapter über die Admin-API (Usage and Cost API).

    GET /v1/organizations/cost_report?starting_at=…&bucket_width=1d&limit=31[&page=…]
    Header: x-api-key (Admin-Key sk-ant-admin…), anthropic-version.
    Antwort: data[] = Tages-Buckets {starting_at, ending_at, results[{amount, currency}]},
    amount als Dezimal-String in Cent; Seiten über has_more/next_page.

    Die Admin-API liefert Kosten der Organisation (nicht je API-Key) und kein Ausgabelimit; der Eintrag
    hat deshalb limit_abfragbar=False (Ampel grau). Ohne Admin-Key entfällt er.

    Die API-Keys aus der Umgebung (ANTHROPIC_API_KEY*) erscheinen immer, nur gekürzt und ohne Verbrauch:
    Anthropic liefert je Key weder Verbrauch noch Limit (Verbrauch im Dashboard der Console).
    """
    anbieter = "anthropic"
    ORGANISATION = "Organisation (Admin-API)"

    def __init__(self, admin_key: str | None = None, transport: Transport | None = None,
                 timeout: float = STANDARD_TIMEOUT, cache_ttl: float = STANDARD_CACHE_TTL,
                 basis_url: str = ANTHROPIC_BASIS, jetzt: Callable[[], datetime] | None = None,
                 api_keys: dict[str, str] | None = None) -> None:
        self._admin_key = anthropic_admin_key() if admin_key is None else (admin_key or None)
        self._api_keys = keys_aus_umgebung("ANTHROPIC_API_KEY") if api_keys is None else dict(api_keys)
        self._client = _Client(transport, timeout, cache_ttl)
        self.basis_url = basis_url.rstrip("/")
        self._jetzt = jetzt or (lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"Anthropic(admin={'ja' if self._admin_key else 'nein'})"

    def eingerichtet(self) -> bool:
        return bool(self._admin_key)

    def _kosten_je_tag(self, seit: datetime) -> dict[str, float]:
        header = {"x-api-key": self._admin_key, "anthropic-version": ANTHROPIC_VERSION}
        tage: dict[str, float] = {}
        seite: str | None = None
        for _ in range(12):
            query = {"starting_at": seit.strftime("%Y-%m-%dT00:00:00Z"), "bucket_width": "1d", "limit": 31}
            if seite:
                query["page"] = seite
            url = f"{self.basis_url}/organizations/cost_report?" + urllib.parse.urlencode(query)
            antwort = self._client.anfrage("GET", url, header, self._admin_key) or {}
            for bucket in antwort.get("data") or []:
                tag = str(bucket.get("starting_at") or "")[:10]
                cent = sum(_zahl(r.get("amount")) or 0.0 for r in bucket.get("results") or []
                           if (r.get("currency") or "USD") == "USD")
                tage[tag] = tage.get(tag, 0.0) + cent / 100.0
            seite = antwort.get("next_page") if antwort.get("has_more") else None
            if not seite:
                break
        return tage

    def keys(self) -> list[KeyStatus]:
        api = [KeyStatus(self.anbieter, n, kennung=kuerze_key(k), limit_abfragbar=False)
               for n, k in self._api_keys.items()]
        if not api and not self._admin_key:
            api = [KeyStatus(self.anbieter, "ANTHROPIC_API_KEY", limit_abfragbar=False, fehler="kein Key")]
        return api + (self._organisation() if self._admin_key else [])

    def _organisation(self) -> list[KeyStatus]:
        name = self.ORGANISATION
        jetzt = self._jetzt()
        monatsanfang = jetzt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        try:
            tage = self._kosten_je_tag(monatsanfang)
        except AnbieterFehler as e:
            return [KeyStatus(self.anbieter, name, kennung=kuerze_key(self._admin_key),
                              limit_abfragbar=False, fehler=str(e))]
        return [KeyStatus(
            self.anbieter, name, kennung=kuerze_key(self._admin_key),
            heute=tage.get(jetzt.strftime("%Y-%m-%d"), 0.0),
            monat=sum(tage.values()),
            limit_abfragbar=False,
        )]

    def konto(self) -> KontoStatus | None:
        return None


# ---------------------------------------------------------------------------
# Bequemer Einstieg
# ---------------------------------------------------------------------------

def adapter_aus_umgebung(transport: Transport | None = None) -> list[Adapter]:
    """Alle Adapter mit Keys aus Umgebung/keyring; Mistral ist bewusst nicht dabei."""
    return [OpenRouter(transport=transport), Anthropic(transport=transport)]
