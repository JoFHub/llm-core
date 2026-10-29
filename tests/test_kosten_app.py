"""Tests der Kosten-App — ohne Netz und ohne echte Keys.

Anbieter über den transport-Parameter gemockt (Muster wie test_anbieter.py), Kurs fest, Kosten-DBs als SQLite
in tmp_path."""
import re
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402

from llm_core import anbieter as an  # noqa: E402
from llm_core.kosten_app import daten, kurs as kurs_modul, logik  # noqa: E402
from llm_core.kosten_app.kurs import Kurs, KursQuelle  # noqa: E402
from llm_core.kosten_app.web import erstelle_app  # noqa: E402

PROV = "sk-or-v1-" + "9" * 60 + "p0p0"
APP_KEY = "sk-or-v1-" + "1" * 60 + "e1e1"
NEU_KLARTEXT = "sk-or-v1-" + "7" * 60 + "n3u4"
BERLIN = ZoneInfo("Europe/Berlin")
JETZT = datetime(2026, 10, 29, 9, 34, tzinfo=BERLIN)
KURS = Kurs(0.9, datum="2026-10-28")


class FakeTransport:
    """Antworten je (Methode, Pfad ohne Query); zeichnet alle Anfragen auf."""

    def __init__(self, antworten: dict | None = None):
        self.antworten = dict(antworten or {})
        self.anfragen: list[dict] = []

    def __call__(self, methode, url, header, body, timeout):
        self.anfragen.append({"methode": methode, "url": url, "body": body})
        pfad = url.split("/api/v1", 1)[-1].split("?", 1)[0]
        antwort = self.antworten.get((methode, pfad))
        if antwort is None:
            return 404, {"error": {"message": "unbekannt"}}
        if isinstance(antwort, BaseException):
            raise antwort
        if callable(antwort):
            return antwort(url, body)
        return antwort

    def aenderungen(self):
        return [a for a in self.anfragen if a["methode"] != "GET"]


def _key(hash_, name, usage, limit, remaining=None, reset=None, disabled=False, daily=0.0, monthly=None):
    return {"hash": hash_, "name": name, "label": f"sk-or-v1-abc...{hash_[-4:]:0>4}", "disabled": disabled,
            "limit": limit, "limit_remaining": (limit - usage) if remaining is None and limit is not None
            else remaining, "limit_reset": reset, "usage": usage, "usage_daily": daily,
            "usage_monthly": usage if monthly is None else monthly}


KEYS_OK = [
    _key("h1", "Memoria", 3.7, 10.0, daily=0.05, monthly=3.0),
    _key("h2", "Probatio-visibilia", 0.4, 10.0, daily=0.0, monthly=1.0),
    _key("h3", "Fabula", 1.0, 5.0, disabled=True, monthly=0.29),
]
CREDITS = (200, {"data": {"total_credits": 30.0, "total_usage": 14.43}})


def _antworten(keys=None, mehr=None):
    a = {("GET", "/keys"): (200, {"data": KEYS_OK if keys is None else keys}), ("GET", "/credits"): CREDITS}
    a.update(mehr or {})
    return a


@pytest.fixture(autouse=True)
def _sauber(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(("OPENROUTER_", "ANTHROPIC_")) or name in (
                "LLM_CORE_EUR_KURS", "LLM_CORE_AMPEL_GELB", "LLM_CORE_AMPEL_ROT",
                "LLM_CORE_KOSTEN_DBS", "LLM_CORE_KONSOLE_DBS"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(an, "_aus_keyring", lambda name: None)
    an.cache_leeren()
    yield
    an.cache_leeren()


@pytest.fixture
def anthropic_db(tmp_path):
    db = tmp_path / "fabula" / "costs.sqlite"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE llm_usage (timestamp TEXT, provider TEXT, cost_usd REAL)")
    conn.executemany("INSERT INTO llm_usage VALUES (?, ?, ?)", [
        ("2026-10-29T06:00:00+00:00", "anthropic", 0.40),    # heute
        ("2026-10-28T23:30:00+00:00", "anthropic", 0.10),    # 00:30 in Berlin: schon heute (Winterzeit +1)
        ("2026-10-05T10:00:00+00:00", "anthropic", 2.233),   # Oktober
        ("2026-09-30T10:00:00+00:00", "anthropic", 50.0),    # Vormonat
        ("2026-10-29T06:00:00+00:00", "openrouter", 9.0),    # anderer Anbieter
    ])
    conn.commit()
    conn.close()
    return db


def _client(transport, provisioning=PROV, kurs=KURS, dbs=(), api_keys=None):
    quelle = KursQuelle(abrufen=lambda: kurs)
    app = erstelle_app(
        openrouter=lambda: an.OpenRouter(api_keys=api_keys or {}, provisioning=provisioning or "",
                                         transport=transport),
        kurs_quelle=quelle, dbs=lambda: list(dbs), jetzt=lambda: JETZT)
    return TestClient(app, follow_redirects=False)


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _plan(html: str) -> str:
    return re.search(r'name="plan" value="([^"]+)"', html).group(1)


# -- Übersicht ------------------------------------------------------------------------------

def test_uebersicht_werte_nur_euro(anthropic_db):
    t = FakeTransport(_antworten())
    r = _client(t, dbs=[anthropic_db]).get("/")
    assert r.status_code == 200
    html = r.text
    assert "$" not in html                                     # nur Euro, auch der Kurs ohne Dollarzeichen
    karte_or = html[html.index("<h2>OpenRouter"):html.index("<h2>Anthropic")]
    assert "0,045 €" in karte_or                               # heute: 0,05 $ * 0,9
    assert "Oktober" in karte_or and "3,86 €" in karte_or      # Monat: 4,29 $ * 0,9
    assert "Guthaben" in karte_or and "14,01 €" in karte_or    # (30 - 14,43) * 0,9
    karte_an = html[html.index("<h2>Anthropic"):html.index("<h2>Keys")]
    assert "0,45 €" in karte_an                                # 0,50 $ heute (Europe/Berlin)
    assert "2,46 €" in karte_an                                # 2,733 $ im Oktober
    assert "berechnet" not in html
    assert "1 USD = 0,90 € (EZB, 28.10.)" in html and "Stand 09:34" in html
    assert 'class="hinweise"' not in html                      # alles in Ordnung: keine Hinweise
    assert "https://openrouter.ai/activity" in html and "https://platform.claude.com/usage" in html


def test_uebersicht_keys_reihenfolge_und_gesperrt():
    keys = KEYS_OK + [_key("h4", "Ohne-Limit", 0.2, None), _key("h5", "Fast", 8.5, 10.0)]
    html = _client(FakeTransport(_antworten(keys))).get("/").text
    namen = re.findall(r'<span class="name">([^<]+)</span>', html)
    assert namen == ["Ohne-Limit", "Fast", "Memoria", "Probatio-visibilia", "Fabula"]
    assert "3,33 €" in html and "37 %" in html            # Memoria: 3,70 $ von 10 $
    fabula = html[html.index(">Fabula<"):]
    assert "gesperrt" in fabula[:300]
    assert 'href="/neu"' in html                              # Verwaltung mit Provisioning-Key


def test_hinweise_nur_bei_problemen():
    s = an.Schwellen()
    ok = an.KeyStatus("openrouter", "a", kennung="x", limit=10, rest=9, aktiv=True)
    assert logik.hinweise(an.KontoStatus("openrouter", 10, 1), [ok], KURS, None, s) == []
    gesperrt_ohne_limit = an.KeyStatus("openrouter", "alt", kennung="y", limit=None, aktiv=False)
    assert logik.hinweise(None, [ok, gesperrt_ohne_limit], KURS, None, s) == []   # gesperrt ist kein Problem
    ohne = an.KeyStatus("openrouter", "ohne", kennung="z", limit=None, aktiv=True)
    voll = an.KeyStatus("openrouter", "voll", kennung="v", limit=10, rest=0, aktiv=True)
    fast = an.KeyStatus("openrouter", "fast", kennung="f", limit=10, rest=1.5, aktiv=True)
    h = logik.hinweise(an.KontoStatus("openrouter", fehler="Zeitüberschreitung nach 10 s"),
                       [ok, ohne, voll, fast], None, "keine Kosten-DB gefunden", s)
    assert [(x.stufe, x.text) for x in h] == [
        ("rot", "Ohne Limit: ohne"), ("rot", "Limit erreicht: voll"), ("orange", "Limit fast erreicht: fast"),
        ("grau", "OpenRouter nicht erreichbar: Zeitüberschreitung nach 10 s"),
        ("grau", "Anthropic: keine Kosten-DB gefunden"),
        ("grau", "Kein Euro-Kurs (EZB nicht erreichbar) — Beträge fehlen, bis ein Kurs da ist.")]


def test_hinweis_schwelle_aus_umgebung(monkeypatch):
    monkeypatch.setenv("LLM_CORE_AMPEL_GELB", "0.3")
    html = _client(FakeTransport(_antworten())).get("/").text
    assert "Limit fast erreicht: Memoria" in html


@pytest.mark.parametrize("fehler, text", [
    ((401, {"error": {"message": "No auth"}}), "Key ungültig oder widerrufen (401)"),
    (TimeoutError("timed out"), "Zeitüberschreitung"),
])
def test_anbieter_fehler(fehler, text):
    t = FakeTransport({("GET", "/keys"): fehler, ("GET", "/credits"): fehler})
    r = _client(t).get("/")
    assert r.status_code == 200
    assert "hinweis grau" in r.text and "OpenRouter nicht erreichbar" in r.text and text in r.text
    assert PROV not in r.text and PROV[-20:] not in r.text


def test_ohne_kurs_keine_betraege_und_kein_aendern():
    t = FakeTransport(_antworten())
    c = _client(t, kurs=None)
    html = c.get("/").text
    karte_or = html[html.index("<h2>OpenRouter"):html.index("<h2>Anthropic")]
    assert "€" not in karte_or and karte_or.count("–") == 3           # heute, Monat, Guthaben
    assert "$" not in html
    assert "Kein Euro-Kurs" in html and "kein Euro-Kurs" in html          # Hinweis oben, Fußzeile
    assert "3,33" not in html and "3,70" not in html                     # weder € noch $ als Ersatz
    seite = c.get("/key/h1").text
    assert "Ohne Euro-Kurs kann das Limit nicht geändert werden" in seite and 'name="limit"' not in seite
    r = c.post("/key/h1/limit", data={"csrf": _csrf(seite), "limit": "5", "reset": "never"})
    assert r.status_code == 400 and t.aenderungen() == []


def test_keine_vollstaendigen_keys_im_html(monkeypatch):
    t = FakeTransport(_antworten(mehr={("POST", "/keys"): (201, {"data": _key("h9", "neu", 0, 5.0), "key": NEU_KLARTEXT})}))
    c = _client(t)
    for pfad in ("/", "/key/h1", "/key/h3", "/neu", "/info"):
        r = c.get(pfad)
        assert r.status_code == 200, pfad
        assert PROV not in r.text and PROV[9:30] not in r.text and NEU_KLARTEXT not in r.text, pfad


# -- Key und Verwaltung ------------------------------------------------------------------------

def test_key_seite():
    html = _client(FakeTransport(_antworten())).get("/key/h1").text
    assert "Memoria" in html and "sk-or-v1-abc...00h1" in html
    assert "3,33 €" in html and "9,00 €" in html and 'value="9,00"' in html   # Limit 10 $ = 9 € Startwert
    assert "Sperren …" in html and "$" not in html


def test_limit_aendern_rechnet_euro_in_dollar_und_braucht_bestaetigung():
    patch = _key("h1", "Memoria", 3.7, 10.0)
    t = FakeTransport(_antworten(mehr={("PATCH", "/keys/h1"): (200, {"data": patch})}))
    c = _client(t)
    csrf = _csrf(c.get("/key/h1").text)
    r = c.post("/key/h1/limit", data={"csrf": csrf, "limit": "9,00", "reset": "monthly"})
    assert r.status_code == 200 and t.aenderungen() == []          # erst der Plan, noch kein Aufruf
    assert "vorher" in r.text and "9,00 €" in r.text and "9,00 € monatlich" in r.text
    assert "$" not in r.text
    r2 = c.post("/bestaetigen", data={"csrf": csrf, "plan": _plan(r.text)})
    assert r2.status_code == 303 and r2.headers["location"] == "/key/h1?meldung=limit"
    [aenderung] = t.aenderungen()
    assert aenderung["methode"] == "PATCH" and aenderung["body"] == {"limit": 10.0, "limit_reset": "monthly"}
    assert "Limit geändert" in c.get(r2.headers["location"]).text


def test_limit_auf_cent_gerundet_und_nie():
    assert logik.eur_zu_usd(7.0, Kurs(0.9123)) == 7.67
    t = FakeTransport(_antworten(mehr={("PATCH", "/keys/h1"): (200, {"data": {"hash": "h1"}})}))
    c = _client(t)
    csrf = _csrf(c.get("/key/h1").text)
    r = c.post("/key/h1/limit", data={"csrf": csrf, "limit": "7", "reset": "never"})
    c.post("/bestaetigen", data={"csrf": csrf, "plan": _plan(r.text)})
    assert t.aenderungen()[0]["body"] == {"limit": 7.78, "limit_reset": None}


def test_bestaetigung_nur_einmal_und_nur_in_eigener_sitzung():
    t = FakeTransport(_antworten(mehr={("PATCH", "/keys/h1"): (200, {"data": {"hash": "h1", "disabled": True}})}))
    c = _client(t)
    csrf = _csrf(c.get("/key/h1").text)
    r = c.post("/key/h1/sperren", data={"csrf": csrf})
    assert "Memoria sperren" in r.text and t.aenderungen() == []
    plan = _plan(r.text)
    fremd = TestClient(c.app, follow_redirects=False)    # andere Sitzung derselben App, fremder Plan
    fremd_csrf = _csrf(fremd.get("/").text)
    assert fremd.post("/bestaetigen", data={"csrf": fremd_csrf, "plan": plan}).status_code == 409
    assert t.aenderungen() == []
    # der Plan ist durch den fremden Versuch verbraucht: auch die eigene Sitzung muss neu planen
    assert c.post("/bestaetigen", data={"csrf": csrf, "plan": plan}).status_code == 409
    r = c.post("/key/h1/sperren", data={"csrf": csrf})
    plan = _plan(r.text)
    assert c.post("/bestaetigen", data={"csrf": csrf, "plan": plan}).status_code == 303
    assert c.post("/bestaetigen", data={"csrf": csrf, "plan": plan}).status_code == 409
    assert [a["body"] for a in t.aenderungen()] == [{"disabled": True}]


def test_csrf_abgewiesen():
    t = FakeTransport(_antworten(mehr={("PATCH", "/keys/h1"): (200, {"data": {"hash": "h1"}})}))
    c = _client(t)
    csrf = _csrf(c.get("/key/h1").text)
    assert c.post("/key/h1/sperren", data={}).status_code == 403
    assert c.post("/key/h1/sperren", data={"csrf": "falsch"}).status_code == 403
    assert c.post("/key/h1/sperren", data={"csrf": csrf},
                  headers={"Origin": "https://boese.example"}).status_code == 403
    assert c.post("/key/h1/sperren", data={"csrf": csrf},
                  headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/key/h1/sperren", data={"csrf": csrf}, headers={"Origin": "null"}).status_code == 403
    assert c.post("/aktualisieren", data={}).status_code == 403
    # Token gehört zur Sitzung: ohne deren Cookie ungültig
    ohne_cookie = _client(t)
    assert ohne_cookie.post("/key/h1/sperren", data={"csrf": csrf}).status_code == 403
    assert t.aenderungen() == []
    assert c.post("/key/h1/sperren", data={"csrf": csrf}, headers={"Origin": "http://testserver",
                                                                   "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_neuer_key_klartext_genau_einmal():
    antwort = (201, {"data": _key("h9", "Neu-App", 0.0, 4.5, reset="monthly"), "key": NEU_KLARTEXT})
    t = FakeTransport(_antworten(mehr={("POST", "/keys"): antwort}))
    c = _client(t)
    csrf = _csrf(c.get("/neu").text)
    r = c.post("/neu", data={"csrf": csrf, "name": "Neu-App", "limit": "4,05", "reset": "monthly"})
    assert r.status_code == 200 and t.aenderungen() == [] and NEU_KLARTEXT not in r.text
    assert "Neu-App · Limit 4,05 € monatlich" in r.text
    plan = _plan(r.text)
    r2 = c.post("/bestaetigen", data={"csrf": csrf, "plan": plan})
    assert r2.status_code == 200 and r2.text.count(NEU_KLARTEXT) == 1
    assert r2.headers["cache-control"] == "no-store"
    assert "Kopieren" in r2.text and ".env" in r2.text
    assert t.aenderungen()[0]["body"] == {"name": "Neu-App", "limit": 4.5, "limit_reset": "monthly"}
    nochmal = c.post("/bestaetigen", data={"csrf": csrf, "plan": plan})   # z. B. Neu laden
    assert nochmal.status_code == 409 and NEU_KLARTEXT not in nochmal.text
    assert len(t.aenderungen()) == 1
    for pfad in ("/", "/key/h9", "/info"):
        assert NEU_KLARTEXT not in c.get(pfad).text


@pytest.mark.parametrize("name, limit, meldung", [
    ("", "5", "Namen"), ("x" * 61, "5", "zu lang"), ("<script>", "5", "Erlaubt"),
    ("ok", "-1", "0 oder größer"), ("ok", "100,01", "Höchstens"), ("ok", "abc", "keine Zahl"), ("ok", "", "Limit"),
])
def test_neuer_key_eingaben_geprueft(name, limit, meldung):
    t = FakeTransport(_antworten())
    c = _client(t)
    csrf = _csrf(c.get("/neu").text)
    r = c.post("/neu", data={"csrf": csrf, "name": name, "limit": limit, "reset": "monthly"})
    assert r.status_code == 400 and meldung in r.text and t.aenderungen() == []
    assert "<script>" not in r.text


def test_eingaben():
    assert logik.lies_limit("12,50") == 12.5
    assert logik.lies_limit(" 12.5 € ") == 12.5
    assert logik.lies_limit("100") == 100.0              # Obergrenze (Nutzer, 2026-10-29)
    with pytest.raises(logik.Eingabefehler, match="Höchstens"):
        logik.lies_limit("1.000")
    assert logik.lies_limit("0") == 0.0
    for falsch in ("nan", "inf", "1e9", "-0,5"):
        with pytest.raises(logik.Eingabefehler):
            logik.lies_limit(falsch)
    assert logik.lies_name("  Probatio   ta ") == "Probatio ta"
    with pytest.raises(logik.Eingabefehler):
        logik.lies_reset("yearly")


def test_ohne_provisioning_key_nur_umgebung(monkeypatch):
    t = FakeTransport({("GET", "/key"): (200, {"data": {"label": "x", "limit": 10, "limit_remaining": 8,
                                                        "usage": 2, "usage_daily": 0.1, "usage_monthly": 1}})})
    c = _client(t, provisioning=None, api_keys={"OPENROUTER_API_KEY_MEMORIA": APP_KEY})
    html = c.get("/").text
    assert "OPENROUTER_API_KEY_MEMORIA" in html and 'href="/neu"' not in html and "Guthaben" not in html
    assert APP_KEY not in html
    seite = c.get("/key/env-OPENROUTER_API_KEY_MEMORIA").text
    assert "sk-or-v1-…e1e1" in seite and "Sperren" not in seite and 'name="limit"' not in seite
    assert APP_KEY not in seite
    assert "Verwaltung aus" in c.get("/info").text
    assert c.post("/key/env-OPENROUTER_API_KEY_MEMORIA/sperren", data={"csrf": _csrf(html)}).status_code == 400
    assert c.post("/neu", data={"csrf": _csrf(html), "name": "x", "limit": "1"}).status_code == 400
    assert t.aenderungen() == []


def test_unbekannter_key_404():
    assert _client(FakeTransport(_antworten())).get("/key/gibtsnicht").status_code == 404


def test_aktualisieren_leert_zwischenspeicher():
    t = FakeTransport(_antworten())
    c = _client(t)
    csrf = _csrf(c.get("/").text)
    c.get("/")
    assert len([a for a in t.anfragen if "/keys" in a["url"]]) == 1   # zweiter Aufruf aus dem Zwischenspeicher
    r = c.post("/aktualisieren", data={"csrf": csrf})
    assert r.status_code == 303 and r.headers["location"] == "/"
    c.get("/")
    assert len([a for a in t.anfragen if "/keys" in a["url"]]) == 2


# -- Gesund, Header, Home-Bildschirm ---------------------------------------------------------------

def test_gesund_ohne_anbieter():
    t = FakeTransport({})
    r = _client(t).get("/gesund")
    assert r.status_code == 200 and r.json()["status"] == "ok" and t.anfragen == []


def test_sicherheits_header_und_cookie():
    c = _client(FakeTransport(_antworten()))
    r = c.get("/")
    h = r.headers
    assert "default-src 'none'" in h["content-security-policy"] and "unsafe-inline" not in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "same-origin"
    assert h["cache-control"] == "no-store"
    cookie = h["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie                                # lokal über http
    # Hinter Caddy (https): Sitzungs-Cookie nur mit Secure
    neu = TestClient(c.app).get("/gesund", headers={"X-Forwarded-Proto": "https"})
    assert "secure" in neu.headers["set-cookie"].lower()


def test_manifest_und_symbole():
    c = _client(FakeTransport(_antworten()))
    html = c.get("/").text
    assert 'rel="manifest"' in html and 'crossorigin="use-credentials"' in html
    assert 'rel="apple-touch-icon"' in html and "viewport-fit=cover" in html
    m = c.get("/manifest.webmanifest").json()
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["scope"] == "/"
    for symbol in m["icons"]:
        assert c.get(symbol["src"]).status_code == 200
    assert c.get("/statisch/symbol-180.png").headers["content-type"] == "image/png"
    assert c.get("/statisch/app.css").status_code == 200
    assert not any(p.startswith("/_anmeldung") for p in (r.path for r in c.app.routes))


# -- Kurs ----------------------------------------------------------------------------------------

def test_kurs_zweimal_am_tag_und_letzter_bekannter(monkeypatch):
    uhr = [0.0]
    antworten = [Kurs(0.9, "2026-10-28"), None, Kurs(0.91, "2026-10-29")]
    aufrufe = []

    def abrufen():
        aufrufe.append(uhr[0])
        return antworten[len(aufrufe) - 1]

    q = KursQuelle(abrufen=abrufen, uhr=lambda: uhr[0])
    assert q.kurs().wert == 0.9
    uhr[0] = 11 * 3600
    assert q.kurs().wert == 0.9 and len(aufrufe) == 1          # innerhalb 12 h keine neue Abfrage
    uhr[0] = 12 * 3600
    k = q.kurs()                                               # Abfrage schlägt fehl: letzter bekannter
    assert k.wert == 0.9 and k.alt and len(aufrufe) == 2
    assert "alt" in logik.kurs_text(k)
    uhr[0] = 12 * 3600 + 1800
    assert q.kurs().alt and len(aufrufe) == 2                  # nach Fehlschlag frühestens nach 1 h
    uhr[0] = 13 * 3600
    assert q.kurs() == Kurs(0.91, "2026-10-29") and len(aufrufe) == 3


def test_kurs_ohne_jeden_kurs_und_fester_kurs(monkeypatch):
    q = KursQuelle(abrufen=lambda: None)
    assert q.kurs() is None
    monkeypatch.setenv("LLM_CORE_EUR_KURS", "0,92")
    assert q.kurs() == Kurs(0.92, quelle="fest")
    assert logik.kurs_text(q.kurs()) == "1 USD = 0,92 € (fest)"
    assert logik.kurs_text(Kurs(0.91234, "2026-10-28")) == "1 USD = 0,9123 € (EZB, 28.10.)"


def test_kurs_abfrage_ohne_netz(monkeypatch):
    def kaputt(*a, **k):
        raise OSError("kein Netz")
    monkeypatch.setattr(kurs_modul.urllib.request, "urlopen", kaputt)
    assert kurs_modul.abfragen() is None


# -- Kosten-DBs ------------------------------------------------------------------------------------

def test_heute_monat_europe_berlin(anthropic_db):
    heute, monat, fehler = daten.heute_monat([anthropic_db], "anthropic", JETZT)
    assert heute == pytest.approx(0.5) and monat == pytest.approx(2.733) and fehler is None
    assert daten.heute_monat([], "anthropic", JETZT) == (None, None, "keine Kosten-DB gefunden")


def test_kosten_seit_mehrere_dbs_je_anbieter(tmp_path):
    from datetime import timezone
    dbs = []
    for name, kosten in (("a", 1.0), ("b", 2.0)):
        db = tmp_path / name / "costs.sqlite"
        db.parent.mkdir()
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE llm_usage (timestamp TEXT, provider TEXT, cost_usd REAL)")
        conn.executemany("INSERT INTO llm_usage VALUES (?, ?, ?)", [
            ("2026-10-29T06:00:00+00:00", "anthropic", kosten),
            ("2026-10-29T06:00:00+00:00", "openrouter", 10.0),
            ("2026-09-30T06:00:00+00:00", "anthropic", 100.0)])
        conn.commit()
        conn.close()
        dbs.append(db)
    seit = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert daten.kosten_seit(dbs, seit, "anthropic") == (pytest.approx(3.0), None)
    summe, fehler = daten.kosten_seit(dbs + [tmp_path / "fehlt" / "costs.sqlite"], seit, "anthropic")
    assert summe == pytest.approx(3.0) and "Kosten-DB nicht lesbar" in fehler
    assert not (tmp_path / "fehlt" / "costs.sqlite").exists()   # nur lesend, nie angelegt


def test_db_kandidaten(tmp_path, monkeypatch):
    from llm_core import cost_tracker
    fabula = tmp_path / "fabula"
    fabula.mkdir()
    (fabula / "costs.sqlite").write_bytes(b"")
    alt = tmp_path / "alt"
    alt.mkdir()
    (alt / "costs.sqlite").write_bytes(b"")
    monkeypatch.setattr(daten, "STANDARD_ORDNER", ())
    monkeypatch.setenv("LLM_CORE_KOSTEN_DBS", f"{fabula}{__import__('os').pathsep}{tmp_path / 'leer'}")
    monkeypatch.setenv("LLM_CORE_KONSOLE_DBS", str(alt))       # alter Name gilt weiter
    cost_tracker.record("p", "m", 0, 0)   # legt costs.sqlite in LLM_CORE_DATA_DIR (= tmp_path) an
    assert daten.db_kandidaten() == [(fabula / "costs.sqlite").resolve(), (alt / "costs.sqlite").resolve(),
                                     (tmp_path / "costs.sqlite").resolve()]


# -- Start --------------------------------------------------------------------------------------

def test_start_nur_localhost(monkeypatch):
    import uvicorn
    from llm_core.kosten_app import __main__ as start
    aufruf = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: aufruf.update(kw))
    monkeypatch.delenv("LLM_CORE_APP_ADRESSE", raising=False)
    monkeypatch.delenv("LLM_CORE_APP_PORT", raising=False)
    assert start.main([]) == 0
    assert aufruf["host"] == "localhost" and aufruf["port"] == 8510 and aufruf["access_log"] is False


def test_llm_core_ohne_web_framework():
    import subprocess
    code = ("import sys, llm_core, llm_core.ui, llm_core.anbieter, llm_core.kosten_app, llm_core.kosten_app.logik,"
            " llm_core.kosten_app.kurs, llm_core.kosten_app.daten;"
            "sys.exit(1 if {'starlette', 'jinja2', 'uvicorn', 'streamlit'} & set(sys.modules) else 0)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_seiten_fragen_anbieter_im_thread_pool_ab():
    """Anbieter antworten blockierend (Timeout bis 10 s): nicht im Event-Loop, sonst stünde auch /gesund."""
    import threading
    threads = []

    def openrouter():
        threads.append(threading.current_thread())
        return an.OpenRouter(api_keys={}, provisioning="", transport=lambda *a: (200, {"data": {}}))

    app = erstelle_app(openrouter=openrouter, kurs_quelle=KursQuelle(abrufen=lambda: KURS), dbs=lambda: [],
                       jetzt=lambda: JETZT)
    with TestClient(app) as c:
        for pfad in ("/", "/info", "/neu"):
            assert c.get(pfad).status_code == 200
    assert len(threads) == 3 and all(t is not threading.main_thread() for t in threads)
    assert all(t.name.startswith("AnyIO worker") for t in threads)
