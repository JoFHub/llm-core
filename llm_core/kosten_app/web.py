"""Web-Schicht der Kosten-App (Starlette + Jinja2), serverseitig gerendert, HTML-Formulare, wenig JavaScript.

Seiten: / (Übersicht), /key/{schluessel}, /neu, /info; POST /aktualisieren, /key/{schluessel}/limit,
/key/{schluessel}/sperren, /neu, /bestaetigen; /gesund für den Healthcheck; /manifest.webmanifest, /statisch/….
Kein Pfad beginnt mit /_anmeldung/ (Anmeldeseite von Caddy).

Sicherheit:
- Kein Key im HTML, in URLs, Meldungen oder Logs; nur die gekürzte Kennung. Ausnahme: der Klartext eines neuen
  Keys, genau einmal in der Antwort auf die Bestätigung (nie gespeichert, Cache-Control: no-store).
- Ändernde Aktionen nur per POST mit CSRF-Token (HMAC der Sitzung, Sitzungs-Cookie SameSite=Strict), dazu
  Prüfung von Origin bzw. Sec-Fetch-Site.
- Jede Änderung in zwei Schritten: der erste POST legt nur einen Plan an (serverseitig, an die Sitzung gebunden,
  10 Minuten gültig, einmal verwendbar); erst POST /bestaetigen ruft den Anbieter mit bestaetigt=True auf.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from llm_core import anbieter as an  # lädt über llm_core.config auch die .env
from llm_core.kosten_app import daten, logik
from llm_core.kosten_app.kurs import Kurs, KursQuelle

ORDNER = Path(__file__).parent
SITZUNG_COOKIE = "kosten_sitzung"
SITZUNG_DAUER = 30 * 24 * 3600
PLAN_DAUER = 600

log = logging.getLogger("llm_core.kosten_app")

try:
    VERSION = version("llm-core")
except PackageNotFoundError:  # pragma: no cover
    VERSION = "0"

CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; manifest-src 'self'; "
       "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

MELDUNGEN = {
    "limit": "Limit geändert.",
    "sperren": "Key gesperrt.",
    "entsperren": "Key entsperrt.",
    "aktualisiert": "Aktualisiert.",
}


class _Plaene:
    """Geplante Änderungen bis zur Bestätigung: Plan-Kennung -> (Plan, Sitzung, Ablauf)."""

    def __init__(self) -> None:
        self._daten: dict[str, tuple[logik.Plan, str, float]] = {}
        self._sperre = threading.Lock()

    def ablegen(self, plan: logik.Plan, sitzung: str) -> str:
        kennung = secrets.token_urlsafe(16)
        jetzt = time.monotonic()
        with self._sperre:
            self._daten = {k: v for k, v in self._daten.items() if v[2] > jetzt}
            self._daten[kennung] = (plan, sitzung, jetzt + PLAN_DAUER)
        return kennung

    def nehmen(self, kennung: str, sitzung: str) -> logik.Plan | None:
        """Plan genau einmal; None, wenn unbekannt, abgelaufen oder aus einer anderen Sitzung."""
        with self._sperre:
            eintrag = self._daten.pop(kennung, None)
        if not eintrag:
            return None
        plan, besitzer, ablauf = eintrag
        if not hmac.compare_digest(besitzer, sitzung) or ablauf < time.monotonic():
            return None
        return plan


def erstelle_app(
    openrouter: Callable[[], an.OpenRouter] | None = None,
    kurs_quelle: KursQuelle | None = None,
    dbs: Callable[[], list[Path]] | None = None,
    jetzt: Callable[[], datetime] | None = None,
) -> Starlette:
    """App mit austauschbaren Quellen (Tests: Anbieter mit Fake-Transport, fester Kurs, DBs in tmp_path)."""
    neuer_openrouter = openrouter or (lambda: an.OpenRouter())
    kurse = kurs_quelle or KursQuelle()
    finde_dbs = dbs or daten.db_kandidaten
    uhr = jetzt or (lambda: datetime.now(daten.ZEITZONE))
    plaene = _Plaene()
    geheim = secrets.token_bytes(32)   # je Prozess; nach einem Neustart sind offene Formulare ungültig

    umgebung = Environment(loader=FileSystemLoader(ORDNER / "vorlagen"), autoescape=select_autoescape(["html"]),
                           trim_blocks=True, lstrip_blocks=True)
    umgebung.globals.update(prozent=logik.prozent, reset_text=logik.reset_text, RESETS=logik.RESETS,
                            version=VERSION, LIMIT_MAX=logik.LIMIT_MAX_EUR)

    # -- Sitzung und CSRF ---------------------------------------------------

    def csrf_token(sitzung: str) -> str:
        return hmac.new(geheim, sitzung.encode(), hashlib.sha256).hexdigest()

    def gleiche_herkunft(request: Request) -> bool:
        seite = request.headers.get("sec-fetch-site")
        if seite and seite not in ("same-origin", "none"):
            return False
        herkunft = request.headers.get("origin")
        if herkunft and herkunft != "null":
            return urlsplit(herkunft).netloc == request.headers.get("host", "")
        return herkunft != "null"

    async def formular(request: Request) -> dict[str, str] | None:
        """Formular eines POST, None bei falschem CSRF-Token oder fremder Herkunft."""
        form = await request.form()
        werte = {k: v for k, v in form.items() if isinstance(v, str)}
        token = werte.get("csrf", "")
        if not gleiche_herkunft(request) or not hmac.compare_digest(token, csrf_token(request.state.sitzung)):
            return None
        return werte

    def abgewiesen(request: Request) -> Response:
        return html(request, "fehler.html", basis(None, titel="Abgelehnt",
                                                  text="Das Formular ist abgelaufen oder kam nicht von dieser Seite. "
                                                       "Bitte die Seite neu laden und noch einmal versuchen."),
                    status=403)

    class Sitzung(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            sitzung = request.cookies.get(SITZUNG_COOKIE, "")
            neu = not (20 <= len(sitzung) <= 64 and sitzung.replace("-", "").replace("_", "").isalnum())
            if neu:
                sitzung = secrets.token_urlsafe(24)
            request.state.sitzung = sitzung
            antwort = await call_next(request)
            if neu:
                https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
                antwort.set_cookie(SITZUNG_COOKIE, sitzung, max_age=SITZUNG_DAUER, httponly=True,
                                   samesite="strict", secure=https, path="/")
            h = antwort.headers
            h.setdefault("Content-Security-Policy", CSP)
            h["X-Content-Type-Options"] = "nosniff"
            # same-origin statt no-referrer: bei no-referrer senden Browser für Formulare „Origin: null“, und die
            # Herkunftsprüfung ginge verloren. Nach außen geht so ebenfalls kein Referrer.
            h["Referrer-Policy"] = "same-origin"
            h["X-Frame-Options"] = "DENY"
            h["Cross-Origin-Opener-Policy"] = "same-origin"
            h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            if not request.url.path.startswith("/statisch/"):
                h["Cache-Control"] = "no-store"
            return antwort

    # -- Hilfen ------------------------------------------------------------

    def html(request: Request, vorlage: str, kontext: dict, status: int = 200) -> Response:
        ctx = {"csrf": csrf_token(request.state.sitzung), **kontext}
        return HTMLResponse(umgebung.get_template(vorlage).render(**ctx), status_code=status)

    def kurs_jetzt() -> Kurs | None:
        return kurse.kurs()

    def eur_fn(kurs: Kurs | None):
        return lambda usd: logik.eur(usd, kurs)

    def finde_key(o: an.OpenRouter, wert: str) -> an.KeyStatus | None:
        for s in o.keys():
            if not logik.ist_anbieter_fehler(s) and logik.schluessel(s) == wert:
                return s
        return None

    def basis(kurs: Kurs | None, **mehr) -> dict:
        return {"kurs": kurs, "eur": eur_fn(kurs), "kurs_text": logik.kurs_text(kurs), **mehr}

    def nicht_gefunden(request: Request, kurs: Kurs | None) -> Response:
        return html(request, "fehler.html", basis(kurs, titel="Nicht gefunden",
                                                  text="Diesen Key gibt es nicht (mehr)."), status=404)

    def key_seite(request: Request, o: an.OpenRouter, status: an.KeyStatus, kurs: Kurs | None,
                  fehler: str | None = None, eingabe: dict | None = None, http: int = 200) -> Response:
        return html(request, "key.html", basis(
            kurs, s=status, zeile=logik.KeyZeile(logik.schluessel(status), status,
                                                  logik.key_ampel(status, an.Schwellen.from_env())),
            limit_text=logik.limit_text(status, kurs), limit_vorschlag=logik.limit_vorschlag(status, kurs),
            verwaltung=o.hat_provisioning and status.id is not None,
            meldung=MELDUNGEN.get(request.query_params.get("meldung", "")), fehler=fehler,
            eingabe=eingabe or {}), status=http)

    def plan_seite(request: Request, plan: logik.Plan, kurs: Kurs | None, zurueck: str) -> Response:
        kennung = plaene.ablegen(plan, request.state.sitzung)
        return html(request, "bestaetigen.html", basis(kurs, plan=plan, plan_id=kennung, zurueck=zurueck))

    # -- Seiten ------------------------------------------------------------

    async def uebersicht(request: Request) -> Response:
        o = neuer_openrouter()
        konto, keys = o.konto(), o.keys()
        kurs = kurs_jetzt()
        u = logik.baue_uebersicht(konto, keys, daten.heute_monat(finde_dbs(), "anthropic", uhr()), kurs, uhr(),
                                  o.hat_provisioning)
        return html(request, "uebersicht.html", basis(
            kurs, u=u, meldung=MELDUNGEN.get(request.query_params.get("meldung", ""))))

    async def aktualisieren(request: Request) -> Response:
        if await formular(request) is None:
            return abgewiesen(request)
        an.cache_leeren()
        kurse.leeren()
        return RedirectResponse("/", status_code=303)

    async def key_ansehen(request: Request) -> Response:
        o = neuer_openrouter()
        kurs = kurs_jetzt()
        status = finde_key(o, request.path_params["schluessel"])
        if status is None:
            return nicht_gefunden(request, kurs)
        return key_seite(request, o, status, kurs)

    async def key_limit(request: Request) -> Response:
        werte = await formular(request)
        if werte is None:
            return abgewiesen(request)
        o = neuer_openrouter()
        kurs = kurs_jetzt()
        status = finde_key(o, request.path_params["schluessel"])
        if status is None:
            return nicht_gefunden(request, kurs)
        if not o.hat_provisioning or status.id is None:
            return key_seite(request, o, status, kurs, fehler="Verwaltung nicht eingerichtet.", http=400)
        if kurs is None:
            return key_seite(request, o, status, kurs, http=400,
                             fehler="Ohne Euro-Kurs kann das Limit nicht geändert werden.")
        try:
            limit = logik.lies_limit(werte.get("limit"))
            reset = logik.lies_reset(werte.get("reset"))
        except logik.Eingabefehler as e:
            return key_seite(request, o, status, kurs, fehler=str(e), eingabe=werte, http=400)
        return plan_seite(request, logik.plan_limit(status, limit, reset, kurs), kurs,
                          f"/key/{logik.schluessel(status)}")

    async def key_sperren(request: Request) -> Response:
        if await formular(request) is None:
            return abgewiesen(request)
        o = neuer_openrouter()
        kurs = kurs_jetzt()
        status = finde_key(o, request.path_params["schluessel"])
        if status is None:
            return nicht_gefunden(request, kurs)
        if not o.hat_provisioning or status.id is None:
            return key_seite(request, o, status, kurs, fehler="Verwaltung nicht eingerichtet.", http=400)
        return plan_seite(request, logik.plan_sperren(status), kurs, f"/key/{logik.schluessel(status)}")

    def neu_seite(request: Request, o: an.OpenRouter, kurs: Kurs | None, fehler: str | None = None,
                  eingabe: dict | None = None, http: int = 200) -> Response:
        return html(request, "neu.html", basis(kurs, verwaltung=o.hat_provisioning, fehler=fehler,
                                               eingabe=eingabe or {}), status=http)

    async def neu_formular(request: Request) -> Response:
        return neu_seite(request, neuer_openrouter(), kurs_jetzt())

    async def neu_planen(request: Request) -> Response:
        werte = await formular(request)
        if werte is None:
            return abgewiesen(request)
        o = neuer_openrouter()
        kurs = kurs_jetzt()
        if not o.hat_provisioning:
            return neu_seite(request, o, kurs, fehler="Verwaltung nicht eingerichtet.", http=400)
        if kurs is None:
            return neu_seite(request, o, kurs, fehler="Ohne Euro-Kurs kann kein Key angelegt werden.", http=400)
        try:
            name = logik.lies_name(werte.get("name"))
            limit = logik.lies_limit(werte.get("limit"))
            reset = logik.lies_reset(werte.get("reset"))
        except logik.Eingabefehler as e:
            return neu_seite(request, o, kurs, fehler=str(e), eingabe=werte, http=400)
        return plan_seite(request, logik.plan_anlegen(name, limit, reset, kurs), kurs, "/neu")

    async def bestaetigen(request: Request) -> Response:
        werte = await formular(request)
        if werte is None:
            return abgewiesen(request)
        kurs = kurs_jetzt()
        plan = plaene.nehmen(werte.get("plan", ""), request.state.sitzung)
        if plan is None:
            return html(request, "fehler.html", basis(
                kurs, titel="Nicht mehr gültig",
                text="Diese Änderung ist abgelaufen oder schon ausgeführt. Bitte noch einmal von vorn."), status=409)
        o = neuer_openrouter()
        erg = logik.ausfuehren(plan, o)
        log.info("Kosten-App: %s %s", plan.aktion, "ok" if erg.ok else "fehlgeschlagen")   # ohne Key, ohne Namen
        if not erg.ok:
            return html(request, "fehler.html", basis(kurs, titel="Nicht geändert",
                                                      text=erg.fehler or "Der Anbieter hat abgelehnt."), status=502)
        if plan.aktion == "anlegen":
            # Klartext genau einmal: nur in dieser Antwort, nie gespeichert (no-store, kein Log)
            return html(request, "neuer_key.html", basis(kurs, name=plan.name, klartext=erg.klartext,
                                                         nachher=erg.nachher))
        return RedirectResponse(f"/key/{plan.key_id}?meldung={plan.aktion}", status_code=303)

    async def info(request: Request) -> Response:
        o = neuer_openrouter()
        kurs = kurs_jetzt()
        return html(request, "info.html", basis(
            kurs, verwaltung=o.hat_provisioning, schwellen=an.Schwellen.from_env(), stand=uhr(),
            dbs=len(finde_dbs()), konsolen=(logik.OPENROUTER_KONSOLE, logik.ANTHROPIC_KONSOLE)))

    async def gesund(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": VERSION})

    async def manifest(request: Request) -> Response:
        return JSONResponse({
            "name": "Kosten", "short_name": "Kosten", "lang": "de", "start_url": "/", "scope": "/",
            "display": "standalone", "background_color": "#f4f5f7", "theme_color": "#f4f5f7",
            "icons": [{"src": "/statisch/symbol-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/statisch/symbol-512.png", "sizes": "512x512", "type": "image/png"},
                      {"src": "/statisch/symbol.svg", "sizes": "any", "type": "image/svg+xml"}],
        }, media_type="application/manifest+json")

    async def apple_symbol(request: Request) -> Response:
        return RedirectResponse("/statisch/symbol-180.png", status_code=301)

    async def nicht_da(request: Request, exc) -> Response:
        return html(request, "fehler.html", basis(None, titel="Nicht gefunden", text="Diese Seite gibt es nicht."),
                    status=404)

    routen = [
        Route("/", uebersicht),
        Route("/aktualisieren", aktualisieren, methods=["POST"]),
        Route("/key/{schluessel}", key_ansehen),
        Route("/key/{schluessel}/limit", key_limit, methods=["POST"]),
        Route("/key/{schluessel}/sperren", key_sperren, methods=["POST"]),
        Route("/neu", neu_formular),
        Route("/neu", neu_planen, methods=["POST"]),
        Route("/bestaetigen", bestaetigen, methods=["POST"]),
        Route("/info", info),
        Route("/gesund", gesund),
        Route("/manifest.webmanifest", manifest),
        Route("/apple-touch-icon.png", apple_symbol),
        Mount("/statisch", StaticFiles(directory=ORDNER / "statisch"), name="statisch"),
    ]
    return Starlette(routes=routen, middleware=[Middleware(Sitzung)], exception_handlers={404: nicht_da})
