"""Anmeldung für MCP-Konnektoren (claude.ai, Custom Connector über Remote-MCP).

claude.ai verlangt OAuth mit Registrierung des Clients (DCR) und PKCE. Der Konnektor ist dabei
sein eigener Autorisierungsserver mit genau einem Benutzer: Eine eigene Anmeldeseite fragt ein
Passwort ab, die Prüfung übergibt das Projekt (``passwort_ok``). Clients und Tokens liegen in
``<ablage>/oauth.json``, damit ein Neustart nicht abmeldet; offene Anmeldungen und Codes nur im
Speicher (sie leben Minuten).

Herkunft: Fabula-Connector (2026-10-31), seit 0.7.0 hier gemeinsam für Fabula und Memoria. Dazu
gekommen sind:

- Fehlversuche begrenzt: nach ``max_fehlversuche`` falschen Passwörtern innerhalb von
  ``sperrzeit`` Sekunden nimmt die Seite bis zum Ende der Sperre kein Passwort mehr an (auch kein
  richtiges, damit Durchprobieren nichts verrät).
- ``oauth.json`` löschen meldet sofort ab, auch bei laufendem Server; ``alle_widerrufen()`` tut
  dasselbe aus dem Programm.
- ``aktiv``: Liefert die Funktion False (Schalter „Konnektor aus“), gilt kein Token, und die
  Anmeldeseite nimmt nichts an.
- Höchstzahl registrierter Clients und offener Anmeldungen, weil DCR ohne Anmeldung offen ist.
- Passwort-Hash im scrypt-Format von Probatio (``scrypt_hash``, ``scrypt_pruefer``).
- Seit 0.7.1 stehen in ``oauth.json`` nur SHA-256-Hashes der Tokens (``sha256:…``), nicht die
  Tokens selbst: Wer die Datei liest (etwa aus einer Sicherung), kann sich damit nicht anmelden.
  Die Tokens sind 32 Byte Zufall, ein Salz ist deshalb nicht nötig. Einträge im Klartext aus
  0.7.0 werden beim Lesen umgewandelt und sofort gehasht zurückgeschrieben.

Benutzung::

    from llm_core.mcp_anmeldung import Anmeldung, scrypt_pruefer
    anmeldung = Anmeldung(ablage=Path(...), url="https://…", passwort_ok=scrypt_pruefer(datei),
                          titel="Memoria für Claude freigeben", text="Claude möchte …")
    mcp = MCPServer(name="…", auth_server_provider=anmeldung, auth=anmeldung.auth_settings())
    anmeldung.seiten_einbinden(mcp)  # /anmelden (GET, POST) und /gesund

Braucht das Extra ``[mcp]`` (mcp 2.3). Ins Log kommen nie Passwörter oder Tokens.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
                                      RefreshToken, TokenError, construct_redirect_uri)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

logger = logging.getLogger("llm_core.mcp_anmeldung")

ZUGANG_DAUER = 3600              # Access-Token: 1 Stunde, danach erneuert Claude per Refresh-Token
ERNEUERN_DAUER = 90 * 24 * 3600  # Refresh-Token: 90 Tage, dann einmal neu anmelden
CODE_DAUER = 300                 # Code und offene Anmeldung: 5 Minuten
MAX_FEHLVERSUCHE = 5             # falsche Passwörter je Sperrzeit
SPERRZEIT = 900                  # Sekunden
MAX_CLIENTS = 20                 # registrierte Clients; die ältesten ohne Token fallen heraus
MAX_ANFRAGEN = 50                # gleichzeitig offene Anmeldungen

# ── Tokens ────────────────────────────────────────────────────────────────────

_HASH_PRAEFIX = "sha256:"


def _h(token: str) -> str:
    """Schlüssel eines Tokens in oauth.json: Hash statt Klartext."""
    return _HASH_PRAEFIX + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _gehasht(schluessel: str) -> str:
    return schluessel if schluessel.startswith(_HASH_PRAEFIX) else _h(schluessel)


# ── Passwort ──────────────────────────────────────────────────────────────────

_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


def scrypt_hash(passwort: str) -> str:
    """Hash im Format von probatio/server/anmeldung.py: "scrypt$n$r$p$salz$wert"."""
    salz = secrets.token_bytes(16)
    wert = hashlib.scrypt(passwort.encode(), salt=salz, maxmem=64 * 1024 * 1024, **_SCRYPT)
    return "$".join(["scrypt", str(_SCRYPT["n"]), str(_SCRYPT["r"]), str(_SCRYPT["p"]), salz.hex(), wert.hex()])


def scrypt_ok(passwort: str, gespeichert: str) -> bool:
    try:
        art, n, r, p, salz, wert = gespeichert.strip().split("$")
        if art != "scrypt":
            return False
        neu = hashlib.scrypt(passwort.encode(), salt=bytes.fromhex(salz), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024)
    except ValueError:
        return False
    return hmac.compare_digest(neu.hex(), wert)


def scrypt_pruefer(datei: Path | Callable[[], Path]) -> Callable[[str], bool]:
    """Prüffunktion gegen eine Hash-Datei. Die Datei wird bei jeder Anmeldung neu gelesen, ein neues
    Passwort gilt also ohne Neustart; fehlt sie, schlägt jede Anmeldung fehl."""
    def pruefen(passwort: str) -> bool:
        pfad = datei() if callable(datei) else datei
        try:
            gespeichert = Path(pfad).read_text(encoding="utf-8")
        except OSError:
            return False
        return bool(passwort) and scrypt_ok(passwort, gespeichert)
    return pruefen


# ── Anmeldeseite ──────────────────────────────────────────────────────────────

_SEITE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titel}</title>
<style>
:root{{--bg:#f5f5f3;--fg:#1c1c1c;--box:#fff;--rand:#d0d0cc;--knopf:#3d5166;--knopf-fg:#fff;--fehler:#a33}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1d1f22;--fg:#e4e4e1;--box:#2a2d31;--rand:#44484e;--knopf:#8aa4bf;--knopf-fg:#1d1f22;--fehler:#e88}}}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}}
main{{width:100%;max-width:22rem;margin:1rem;padding:1.5rem 1.75rem;background:var(--box);border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.12)}}
h1{{font-size:1.25rem;margin:0 0 1rem}}p{{line-height:1.5;margin:.5rem 0}}
label{{display:block;font-size:.9rem;margin:.75rem 0 .25rem}}
input{{box-sizing:border-box;width:100%;padding:.55rem .6rem;font-size:1rem;border:1px solid var(--rand);border-radius:6px;background:var(--bg);color:var(--fg)}}
button{{margin-top:1.1rem;padding:.55rem 1.1rem;font-size:1rem;border:0;border-radius:6px;background:var(--knopf);color:var(--knopf-fg);cursor:pointer}}
.fehler{{color:var(--fehler)}}
</style></head><body><main><h1>{titel}</h1>{inhalt}</main></body></html>"""

_ABGELAUFEN = "<p>Diese Anmeldung ist abgelaufen. Bitte in Claude neu verbinden.</p>"
_AUS = "<p>Der Zugang ist gerade ausgeschaltet.</p>"


class Anmeldung:
    """OAuthAuthorizationServerProvider des MCP-SDK für einen Benutzer.

    ablage: Ordner für oauth.json (oder Funktion, die ihn liefert, z. B. aus einer Umgebungsvariable).
    url: öffentliche Adresse des Konnektors ohne /mcp.
    passwort_ok: prüft das Passwort der Anmeldeseite.
    aktiv: False = alle Tokens ungültig, keine Anmeldung (Schalter „aus“).
    """

    def __init__(self, ablage: Path | Callable[[], Path], url: str, passwort_ok: Callable[[str], bool], *,
                 titel: str = "Zugang für Claude freigeben",
                 text: str = "Claude möchte auf deine Daten zugreifen. Melde dich mit dem Passwort an.",
                 scope: str = "mcp", subject: str = "benutzer", benutzer: str = "benutzer",
                 aktiv: Callable[[], bool] | None = None,
                 zugang_dauer: int = ZUGANG_DAUER, erneuern_dauer: int = ERNEUERN_DAUER,
                 code_dauer: int = CODE_DAUER, max_fehlversuche: int = MAX_FEHLVERSUCHE,
                 sperrzeit: int = SPERRZEIT, bremse: float = 1.0,
                 uhr: Callable[[], float] = time.time) -> None:
        self._ablage = ablage
        self.url = url.rstrip("/")
        self._passwort_ok = passwort_ok
        self.titel, self.text, self.scope, self.subject, self.benutzer = titel, text, scope, subject, benutzer
        self._aktiv = aktiv or (lambda: True)
        self.zugang_dauer, self.erneuern_dauer, self.code_dauer = zugang_dauer, erneuern_dauer, code_dauer
        self.max_fehlversuche, self.sperrzeit, self.bremse = max_fehlversuche, sperrzeit, bremse
        self._uhr = uhr
        self._sperre = threading.Lock()
        self._anfragen: dict[str, tuple[float, str, AuthorizationParams]] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._fehlversuche: list[float] = []
        self._daten: dict = {}
        self._stand: tuple | None = None  # (Pfad, mtime_ns) der zuletzt gelesenen Datei
        self._frisch()

    # ── Ablage ──

    @property
    def datei(self) -> Path:
        ordner = self._ablage() if callable(self._ablage) else self._ablage
        return Path(ordner) / "oauth.json"

    def _frisch(self) -> None:
        """Datei neu lesen, wenn sie sich geändert hat; fehlt sie, ist niemand mehr angemeldet."""
        datei = self.datei
        try:
            stand = (str(datei), datei.stat().st_mtime_ns)
        except OSError:
            stand = (str(datei), None)
        if stand != self._stand:
            try:
                self._daten = json.loads(datei.read_text(encoding="utf-8")) if stand[1] else {}
            except (OSError, ValueError):
                self._daten = {}
            self._stand = stand
        for teil in ("clients", "zugang", "erneuern"):
            self._daten.setdefault(teil, {})
        if self._klartext_umwandeln():
            self._speichern()

    def _klartext_umwandeln(self) -> bool:
        """Tokens aus 0.7.0 (Klartext als Schlüssel) in Hashes umwandeln. True, wenn etwas umgewandelt wurde."""
        alt = any(not k.startswith(_HASH_PRAEFIX) for teil in ("zugang", "erneuern") for k in self._daten[teil])
        alt = alt or any(v.get("erneuern") and not v["erneuern"].startswith(_HASH_PRAEFIX)
                         for v in self._daten["zugang"].values())
        if not alt:
            return False
        self._daten["erneuern"] = {_gehasht(k): v for k, v in self._daten["erneuern"].items()}
        self._daten["zugang"] = {_gehasht(k): {**v, **({"erneuern": _gehasht(v["erneuern"])} if v.get("erneuern") else {})}
                                 for k, v in self._daten["zugang"].items()}
        logger.info("Anmeldung: Tokens in oauth.json auf Hashes umgestellt")
        return True

    def _speichern(self) -> None:
        datei = self.datei
        datei.parent.mkdir(parents=True, exist_ok=True)
        jetzt = self._uhr()
        for teil in ("zugang", "erneuern"):  # Abgelaufenes nicht ewig mitschleppen
            self._daten[teil] = {k: v for k, v in self._daten[teil].items()
                                 if not v.get("expires_at") or v["expires_at"] > jetzt}
        tmp = datei.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._daten, indent=1), encoding="utf-8")
        tmp.replace(datei)
        self._stand = (str(datei), datei.stat().st_mtime_ns)

    def aktiv(self) -> bool:
        try:
            return bool(self._aktiv())
        except Exception:  # Schalter nicht lesbar: lieber zu
            logger.exception("Anmeldung: Schalter nicht lesbar")
            return False

    def alle_widerrufen(self) -> int:
        """Alle Clients und Tokens löschen (Datei weg). Gibt die Zahl der gültigen Erneuerungs-Tokens zurück."""
        with self._sperre:
            self._frisch()
            anzahl = len(self.angemeldet())
            self._daten = {"clients": {}, "zugang": {}, "erneuern": {}}
            self._anfragen.clear()
            self._codes.clear()
            self.datei.unlink(missing_ok=True)
            self._stand = (str(self.datei), None)
        logger.info("Anmeldung: alle Tokens widerrufen (%d)", anzahl)
        return anzahl

    def angemeldet(self) -> list[dict]:
        """Gültige Erneuerungs-Tokens (je Anmeldung eins), ohne die Tokens selbst."""
        jetzt = self._uhr()
        return [{"client_id": v.get("client_id"), "expires_at": v.get("expires_at")}
                for v in self._daten.get("erneuern", {}).values() if v.get("expires_at", 0) > jetzt]

    # ── Fehlversuche ──

    def gesperrt_bis(self) -> float | None:
        jetzt = self._uhr()
        self._fehlversuche = [t for t in self._fehlversuche if t > jetzt - self.sperrzeit]
        if len(self._fehlversuche) >= self.max_fehlversuche:
            return self._fehlversuche[0] + self.sperrzeit
        return None

    def passwort_pruefen(self, passwort: str) -> bool:
        """Prüft das Passwort unter Beachtung der Sperre. False auch, solange gesperrt ist."""
        if self.gesperrt_bis() is not None:
            return False
        if self._passwort_ok(passwort):
            self._fehlversuche.clear()
            return True
        self._fehlversuche.append(self._uhr())
        logger.warning("Anmeldung fehlgeschlagen (%d von %d)", len(self._fehlversuche), self.max_fehlversuche)
        if self.gesperrt_bis() is not None:
            logger.warning("Anmeldung gesperrt für %d Minuten", self.sperrzeit // 60)
        return False

    # ── OAuthAuthorizationServerProvider ──

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._sperre:
            self._frisch()
            roh = self._daten["clients"].get(client_id)
        return OAuthClientInformationFull.model_validate(roh) if roh else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with self._sperre:
            self._frisch()
            clients = self._daten["clients"]
            clients[client_info.client_id] = client_info.model_dump(mode="json")
            if len(clients) > MAX_CLIENTS:  # DCR ist offen: Datei nicht beliebig wachsen lassen
                mit_token = {v.get("client_id") for teil in ("zugang", "erneuern")
                             for v in self._daten[teil].values()}
                frei = sorted((c.get("client_id_issued_at") or 0, k) for k, c in clients.items()
                              if k not in mit_token and k != client_info.client_id)
                for _, k in frei[:len(clients) - MAX_CLIENTS]:
                    clients.pop(k, None)
            self._speichern()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        jetzt = self._uhr()
        self._anfragen = {k: v for k, v in self._anfragen.items() if v[0] > jetzt}
        while len(self._anfragen) >= MAX_ANFRAGEN:
            self._anfragen.pop(next(iter(self._anfragen)))
        anfrage = secrets.token_urlsafe(24)
        self._anfragen[anfrage] = (jetzt + self.code_dauer, client.client_id, params)
        return f"{self.url}/anmelden?anfrage={anfrage}"

    def anfrage_offen(self, anfrage: str) -> bool:
        eintrag = self._anfragen.get(anfrage)
        return bool(eintrag) and eintrag[0] > self._uhr()

    def anmeldung_abschliessen(self, anfrage: str) -> str | None:
        """Nach richtigem Passwort: Code erzeugen, Adresse zurück zu Claude."""
        eintrag = self._anfragen.pop(anfrage, None)
        if not eintrag or eintrag[0] < self._uhr() or not self.aktiv():
            return None
        _, client_id, params = eintrag
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [self.scope], expires_at=self._uhr() + self.code_dauer,
            client_id=client_id, code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource, subject=self.subject)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if code and code.client_id == client.client_id and code.expires_at > self._uhr() and self.aktiv():
            return code
        return None

    def _tokens(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        jetzt = int(self._uhr())
        zugang, erneuern = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        gemeinsam = {"client_id": client_id, "scopes": scopes, "resource": resource, "subject": self.subject}
        with self._sperre:
            self._frisch()
            self._daten["zugang"][_h(zugang)] = {**gemeinsam, "expires_at": jetzt + self.zugang_dauer,
                                                 "erneuern": _h(erneuern)}
            self._daten["erneuern"][_h(erneuern)] = {**gemeinsam, "expires_at": jetzt + self.erneuern_dauer}
            self._speichern()
        return OAuthToken(access_token=zugang, expires_in=self.zugang_dauer, scope=" ".join(scopes),
                          refresh_token=erneuern)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        if self._codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "Code schon benutzt")
        return self._tokens(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> RefreshToken | None:
        if not self.aktiv():
            return None
        with self._sperre:
            self._frisch()
            roh = self._daten["erneuern"].get(_h(refresh_token))
        if not roh or roh["client_id"] != client.client_id or roh["expires_at"] < self._uhr():
            return None
        return RefreshToken(token=refresh_token, **roh)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        with self._sperre:  # beide erneuern (Rotation)
            self._frisch()
            schluessel = _h(refresh_token.token)
            self._daten["erneuern"].pop(schluessel, None)
            self._daten["zugang"] = {k: v for k, v in self._daten["zugang"].items()
                                     if v.get("erneuern") != schluessel}
        return self._tokens(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        if not self.aktiv():
            return None
        with self._sperre:
            self._frisch()
            roh = self._daten["zugang"].get(_h(token))
        if not roh or roh["expires_at"] < self._uhr():
            return None
        return AccessToken(token=token, **{k: v for k, v in roh.items() if k != "erneuern"})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self._sperre:
            self._frisch()
            schluessel = _h(token.token)
            erneuern = (self._daten["zugang"].get(schluessel) or {}).get("erneuern", schluessel)
            self._daten["erneuern"].pop(erneuern, None)
            self._daten["zugang"] = {k: v for k, v in self._daten["zugang"].items()
                                     if k != schluessel and v.get("erneuern") != erneuern}
            self._speichern()

    # ── Einbinden ──

    def auth_settings(self) -> AuthSettings:
        return AuthSettings(
            issuer_url=self.url,
            resource_server_url=f"{self.url}/mcp",
            validate_token_resource=False,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[self.scope], default_scopes=[self.scope]),
            revocation_options=RevocationOptions(enabled=True),
        )

    def _seite(self, inhalt: str, status_code: int = 200) -> HTMLResponse:
        return HTMLResponse(_SEITE.format(titel=html.escape(self.titel), inhalt=inhalt), status_code=status_code,
                            headers={"Cache-Control": "no-store"})

    def _formular(self, anfrage: str, fehler: str = "") -> HTMLResponse:
        hinweis = f'<p class="fehler">{html.escape(fehler)}</p>' if fehler else ""
        return self._seite(f"""{hinweis}
<p>{html.escape(self.text)}</p>
<form method="post" action="/anmelden">
<input type="hidden" name="anfrage" value="{html.escape(anfrage)}">
<input type="text" name="benutzer" value="{html.escape(self.benutzer)}" autocomplete="username" hidden>
<label for="passwort">Passwort</label>
<input id="passwort" name="passwort" type="password" autocomplete="current-password" required autofocus>
<button type="submit">Freigeben</button>
</form>""")

    def _gesperrt_text(self, bis: float) -> str:
        minuten = max(1, int((bis - self._uhr() + 59) // 60))
        return f"Zu viele Fehlversuche. Die Anmeldung ist für {minuten} Minuten gesperrt."

    def seiten_einbinden(self, mcp) -> None:
        """Anmeldeseite (/anmelden) und /gesund am MCPServer anmelden."""

        @mcp.custom_route("/anmelden", methods=["GET"])
        async def anmelden_seite(request: Request) -> Response:
            if not self.aktiv():
                return self._seite(_AUS, 403)
            anfrage = request.query_params.get("anfrage", "")
            if not self.anfrage_offen(anfrage):
                return self._seite(_ABGELAUFEN, 400)
            bis = self.gesperrt_bis()
            return self._formular(anfrage, self._gesperrt_text(bis) if bis else "")

        @mcp.custom_route("/anmelden", methods=["POST"])
        async def anmelden(request: Request) -> Response:
            if not self.aktiv():
                return self._seite(_AUS, 403)
            form = await request.form()
            anfrage, pw = str(form.get("anfrage", "")), str(form.get("passwort", ""))
            if not self.anfrage_offen(anfrage):
                return self._seite(_ABGELAUFEN, 400)
            if (bis := self.gesperrt_bis()) is not None:
                return self._formular(anfrage, self._gesperrt_text(bis))
            if not self.passwort_pruefen(pw):
                await asyncio.sleep(self.bremse)  # bremst das Durchprobieren
                bis = self.gesperrt_bis()
                return self._formular(anfrage, self._gesperrt_text(bis) if bis
                                      else "Passwort falsch. Bitte noch einmal versuchen.")
            ziel = self.anmeldung_abschliessen(anfrage)
            if ziel is None:
                return self._seite(_ABGELAUFEN, 400)
            logger.info("Anmeldung erfolgreich")
            return RedirectResponse(ziel, status_code=302)

        @mcp.custom_route("/gesund", methods=["GET"])
        async def gesund(request: Request) -> Response:
            return PlainTextResponse("ok")
