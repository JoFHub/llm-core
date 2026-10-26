"""Tests für llm_core.anbieter — ohne Netz, Anbieter-Antworten als Fixtures."""
from datetime import datetime, timezone

import pytest

from llm_core import anbieter as an

KEY = "sk-or-v1-" + "0" * 60 + "a1b2"
PROV = "sk-or-v1-" + "9" * 60 + "p0p0"
ADMIN = "sk-ant-admin01-" + "x" * 80 + "AdM1"


class FakeTransport:
    """Antworten je (Methode, Pfad ohne Query); zeichnet alle Anfragen auf."""

    def __init__(self, antworten: dict | None = None):
        self.antworten = antworten or {}
        self.anfragen: list[dict] = []

    def __call__(self, methode, url, header, body, timeout):
        self.anfragen.append({"methode": methode, "url": url, "header": header, "body": body, "timeout": timeout})
        pfad = url.split("/api/v1", 1)[-1].split("/v1", 1)[-1].split("?", 1)[0]
        antwort = self.antworten.get((methode, pfad))
        if antwort is None:
            return 404, {"error": {"message": "unbekannt"}}
        if isinstance(antwort, BaseException):
            raise antwort
        if callable(antwort):
            return antwort(url, body)
        return antwort


@pytest.fixture(autouse=True)
def _sauber(monkeypatch):
    an.cache_leeren()
    for name in ("OPENROUTER_PROVISIONING_KEY", "OPENROUTER_MANAGEMENT_KEY", "ANTHROPIC_ADMIN_KEY",
                 "LLM_CORE_AMPEL_GELB", "LLM_CORE_AMPEL_ROT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(an, "_aus_keyring", lambda name: None)
    yield
    an.cache_leeren()


KEY_ANTWORT = (200, {"data": {
    "label": "sk-or-v1-000...a1b2", "limit": 10, "limit_remaining": 5.9, "limit_reset": None,
    "usage": 4.1, "usage_daily": 0.35, "usage_weekly": 1.2, "usage_monthly": 3.1, "is_free_tier": False}})

KEYS_ANTWORT = (200, {"data": [
    {"hash": "h1", "name": "probatio-visibilia", "label": "sk-or-v1-abc...a1b2", "disabled": False,
     "limit": 10, "limit_remaining": 1.5, "limit_reset": None,
     "usage": 8.5, "usage_daily": 0.5, "usage_weekly": 2, "usage_monthly": 6},
    {"hash": "h2", "name": "fabula", "label": "sk-or-v1-def...c3d4", "disabled": True,
     "limit": None, "limit_remaining": None, "limit_reset": None,
     "usage": 1.0, "usage_daily": 0, "usage_weekly": 0, "usage_monthly": 0.2},
]})


def _status(**kw):
    return an.KeyStatus("openrouter", "t", **kw)


# -- Ampel ----------------------------------------------------------------------

@pytest.mark.parametrize("verbraucht, erwartet", [
    (0.0, an.GRUEN), (7.99, an.GRUEN), (8.0, an.GELB), (9.99, an.GELB), (10.0, an.ROT), (12.0, an.ROT),
])
def test_ampel_grenzen(verbraucht, erwartet):
    assert an.ampel(_status(limit=10.0, verbrauch_gesamt=verbraucht)) == erwartet


def test_ampel_ohne_limit_ist_rot():
    assert an.ampel(_status(limit=None, verbrauch_gesamt=0.0)) == an.ROT


def test_ampel_fehler_und_nicht_abfragbar_grau():
    assert an.ampel(_status(limit=10.0, verbrauch_gesamt=1.0, fehler="401")) == an.GRAU
    assert an.ampel(_status(limit=None, limit_abfragbar=False, monat=3.0)) == an.GRAU


def test_ampel_schwellen_einstellbar(monkeypatch):
    s = _status(limit=10.0, verbrauch_gesamt=6.0)
    assert an.ampel(s, an.Schwellen(gelb=0.5)) == an.GELB
    assert an.ampel(s, an.Schwellen(gelb=0.5, rot=0.6)) == an.ROT
    monkeypatch.setenv("LLM_CORE_AMPEL_GELB", "0.5")
    assert an.ampel(s) == an.GELB


def test_ampel_limit_mit_zuruecksetzen_zaehlt_periode():
    # Gesamtverbrauch 50 $, aber monatliches Limit 10 $ mit 9 $ Rest -> 10 % verbraucht
    s = _status(limit=10.0, rest=9.0, verbrauch_gesamt=50.0, limit_reset="monthly")
    assert s.verbrauch_im_limit == pytest.approx(1.0)
    assert an.ampel(s) == an.GRUEN


# -- Kürzen -------------------------------------------------------------------

def test_kuerze_key():
    assert an.kuerze_key(KEY) == "sk-or-v1-…a1b2"
    assert an.kuerze_key(ADMIN) == "…AdM1"
    assert an.kuerze_key("") == ""
    assert "0000" not in an.kuerze_key(KEY)


def test_keys_aus_umgebung(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY_VISIBILIA", "b")
    monkeypatch.setenv("OPENROUTER_API_KEY_LEER", " ")
    keys = an.keys_aus_umgebung()
    assert keys["OPENROUTER_API_KEY"] == "a" and keys["OPENROUTER_API_KEY_VISIBILIA"] == "b"
    assert "OPENROUTER_API_KEY_LEER" not in keys


def test_provisioning_key_aus_umgebung_und_keyring(monkeypatch):
    assert an.provisioning_key() is None
    monkeypatch.setattr(an, "_aus_keyring", lambda name: "aus-keyring")
    assert an.provisioning_key() == "aus-keyring"
    monkeypatch.setenv("OPENROUTER_PROVISIONING_KEY", PROV)
    assert an.provisioning_key() == PROV


# -- OpenRouter: Abfragen -------------------------------------------------------

def test_key_status():
    t = FakeTransport({("GET", "/key"): KEY_ANTWORT})
    s = an.OpenRouter(api_keys={}, provisioning="", transport=t).key_status(KEY, "VISIBILIA")
    assert (s.name, s.kennung) == ("VISIBILIA", "sk-or-v1-…a1b2")
    assert (s.verbrauch_gesamt, s.heute, s.monat, s.limit, s.rest) == (4.1, 0.35, 3.1, 10.0, 5.9)
    assert s.fehler is None
    assert an.ampel(s) == an.GRUEN
    a = t.anfragen[0]
    assert a["url"] == "https://openrouter.ai/api/v1/key"
    assert a["header"]["Authorization"] == f"Bearer {KEY}"
    assert a["timeout"] == an.STANDARD_TIMEOUT


@pytest.mark.parametrize("status, text", [
    (401, "401"), (402, "402"), (403, "403"), (429, "429"), (503, "503"),
])
def test_key_status_http_fehler_wird_zurueckgegeben(status, text):
    t = FakeTransport({("GET", "/key"): (status, {"error": {"message": f"kaputt {KEY}"}})})
    s = an.OpenRouter(api_keys={}, provisioning="", transport=t).key_status(KEY)
    assert s.fehler and text in s.fehler
    assert KEY not in s.fehler           # Key nie in der Fehlermeldung
    assert an.ampel(s) == an.GRAU


def test_key_status_timeout_und_netz():
    t = FakeTransport({("GET", "/key"): TimeoutError("timed out")})
    s = an.OpenRouter(api_keys={}, provisioning="", transport=t, timeout=2).key_status(KEY)
    assert "Zeitüberschreitung" in s.fehler and "2 s" in s.fehler
    an.cache_leeren()
    t = FakeTransport({("GET", "/key"): OSError(f"refused for {KEY}")})
    s = an.OpenRouter(api_keys={}, provisioning="", transport=t).key_status(KEY)
    assert "Netzwerkfehler" in s.fehler and KEY not in s.fehler


def test_zwischenspeicher():
    t = FakeTransport({("GET", "/key"): KEY_ANTWORT})
    o = an.OpenRouter(api_keys={}, provisioning="", transport=t)
    o.key_status(KEY)
    o.key_status(KEY)
    assert len(t.anfragen) == 1
    an.cache_leeren()
    o.key_status(KEY)
    assert len(t.anfragen) == 2
    ohne = an.OpenRouter(api_keys={}, provisioning="", transport=t, cache_ttl=0)
    ohne.key_status(KEY)
    ohne.key_status(KEY)
    assert len(t.anfragen) == 4


def test_zwischenspeicher_laeuft_ab(monkeypatch):
    uhr = [1000.0]
    monkeypatch.setattr(an.time, "monotonic", lambda: uhr[0])
    t = FakeTransport({("GET", "/key"): KEY_ANTWORT})
    o = an.OpenRouter(api_keys={}, provisioning="", transport=t, cache_ttl=60)
    o.key_status(KEY)
    uhr[0] += 59
    o.key_status(KEY)
    assert len(t.anfragen) == 1
    uhr[0] += 2
    o.key_status(KEY)
    assert len(t.anfragen) == 2


def test_fehler_wird_nicht_zwischengespeichert():
    t = FakeTransport({("GET", "/key"): (500, None)})
    o = an.OpenRouter(api_keys={}, provisioning="", transport=t)
    assert o.key_status(KEY).fehler
    t.antworten[("GET", "/key")] = KEY_ANTWORT
    assert o.key_status(KEY).fehler is None


def test_ohne_provisioning_nur_umgebungs_keys():
    t = FakeTransport({("GET", "/key"): KEY_ANTWORT})
    o = an.OpenRouter(api_keys={"OPENROUTER_API_KEY_VISIBILIA": KEY}, provisioning="", transport=t)
    assert not o.hat_provisioning and o.eingerichtet()
    keys = o.keys()
    assert [k.name for k in keys] == ["OPENROUTER_API_KEY_VISIBILIA"]
    assert o.konto() is None
    assert all("/keys" not in a["url"] for a in t.anfragen)


def test_alle_keys_mit_provisioning():
    t = FakeTransport({("GET", "/keys"): KEYS_ANTWORT})
    o = an.OpenRouter(api_keys={}, provisioning=PROV, transport=t)
    keys = o.keys()
    assert [(k.id, k.name, k.aktiv) for k in keys] == [("h1", "probatio-visibilia", True), ("h2", "fabula", False)]
    assert an.ampel(keys[0]) == an.GELB      # 8,5 von 10
    assert an.ampel(keys[1]) == an.ROT       # ohne Limit
    a = t.anfragen[0]
    assert a["header"]["Authorization"] == f"Bearer {PROV}"
    assert "offset=0" in a["url"] and "include_disabled=true" in a["url"]


def test_alle_keys_blaettert():
    def seite(url, body):
        offset = int(url.split("offset=")[1].split("&")[0])
        anzahl = 100 if offset == 0 else 3
        return 200, {"data": [{"hash": f"h{offset + i}", "name": "k", "limit": 1, "usage": 0}
                              for i in range(anzahl)]}
    t = FakeTransport({("GET", "/keys"): seite})
    keys = an.OpenRouter(api_keys={}, provisioning=PROV, transport=t).alle_keys()
    assert len(keys) == 103 and len(t.anfragen) == 2


def test_alle_keys_fehler():
    t = FakeTransport({("GET", "/keys"): (401, {"error": {"message": "Invalid key"}})})
    keys = an.OpenRouter(api_keys={}, provisioning=PROV, transport=t).alle_keys()
    assert len(keys) == 1 and "401" in keys[0].fehler and PROV not in keys[0].fehler


def test_konto():
    t = FakeTransport({("GET", "/credits"): (200, {"data": {"total_credits": 50, "total_usage": 42.5}})})
    k = an.OpenRouter(api_keys={}, provisioning=PROV, transport=t).konto()
    assert (k.guthaben, k.verbraucht, k.rest, k.fehler) == (50.0, 42.5, 7.5, None)
    t = FakeTransport({("GET", "/credits"): (402, None)})
    an.cache_leeren()
    assert "402" in an.OpenRouter(api_keys={}, provisioning=PROV, transport=t).konto().fehler


def test_repr_ohne_keys():
    o = an.OpenRouter(api_keys={"OPENROUTER_API_KEY": KEY}, provisioning=PROV)
    assert KEY not in repr(o) and PROV not in repr(o)
    assert ADMIN not in repr(an.Anthropic(admin_key=ADMIN))


# -- OpenRouter: Verwaltung ------------------------------------------------------

def _verwaltung(extra: dict) -> tuple[an.OpenRouter, FakeTransport]:
    t = FakeTransport({("GET", "/keys"): KEYS_ANTWORT, **extra})
    return an.OpenRouter(api_keys={}, provisioning=PROV, transport=t), t


def test_verwaltung_braucht_bestaetigung():
    o, t = _verwaltung({})
    with pytest.raises(an.BestaetigungFehlt):
        o.limit_aendern("h1", 20)
    with pytest.raises(an.BestaetigungFehlt):
        o.key_aktivieren("h1", False)
    with pytest.raises(an.BestaetigungFehlt):
        o.key_anlegen("neu", 5)
    assert t.anfragen == []


def test_limit_aendern():
    neu = dict(KEYS_ANTWORT[1]["data"][0], limit=20, limit_remaining=11.5, limit_reset="monthly")
    o, t = _verwaltung({("PATCH", "/keys/h1"): (200, {"data": neu})})
    o.alle_keys()                                   # füllt den Zwischenspeicher
    erg = o.limit_aendern("h1", 20, limit_reset="monthly", bestaetigt=True)
    assert erg.ok and erg.vorher.limit == 10.0 and erg.nachher.limit == 20.0
    patch = [a for a in t.anfragen if a["methode"] == "PATCH"][0]
    assert patch["url"] == "https://openrouter.ai/api/v1/keys/h1"
    assert patch["body"] == {"limit": 20, "limit_reset": "monthly"}
    assert patch["header"]["Authorization"] == f"Bearer {PROV}"
    # Änderung leert den Zwischenspeicher: nächste Liste wird neu geholt
    vorher = len(t.anfragen)
    o.alle_keys()
    assert len(t.anfragen) == vorher + 1


def test_limit_entfernen_sendet_null():
    o, t = _verwaltung({("PATCH", "/keys/h1"): (200, {"data": {"hash": "h1", "limit": None}})})
    o.limit_aendern("h1", None, bestaetigt=True)
    assert [a for a in t.anfragen if a["methode"] == "PATCH"][0]["body"] == {"limit": None}


def test_key_deaktivieren_und_aktivieren():
    o, t = _verwaltung({("PATCH", "/keys/h1"): (200, {"data": {"hash": "h1", "disabled": True}}),
                        ("PATCH", "/keys/h2"): (200, {"data": {"hash": "h2", "disabled": False}})})
    erg = o.key_aktivieren("h1", False, bestaetigt=True)
    assert erg.ok and erg.aktion == "deaktivieren" and erg.vorher.aktiv is True and erg.nachher.aktiv is False
    erg = o.key_aktivieren("h2", True, bestaetigt=True)
    assert erg.ok and erg.nachher.aktiv is True
    bodies = [a["body"] for a in t.anfragen if a["methode"] == "PATCH"]
    assert bodies == [{"disabled": True}, {"disabled": False}]


def test_verwaltung_fehler():
    o, _ = _verwaltung({("PATCH", "/keys/h1"): (401, {"error": {"message": "nope"}})})
    erg = o.limit_aendern("h1", 5, bestaetigt=True)
    assert not erg.ok and "401" in erg.fehler and erg.vorher.id == "h1"


def test_key_anlegen():
    neu = "sk-or-v1-" + "7" * 60 + "n3w1"
    o, t = _verwaltung({("POST", "/keys"): (201, {"data": {"hash": "h3", "name": "memoria", "label": "",
                                                          "limit": 5, "usage": 0, "disabled": False},
                                                 "key": neu})})
    erg = o.key_anlegen(" memoria ", 5, bestaetigt=True)
    assert erg.ok and erg.klartext == neu
    assert erg.nachher.kennung == "sk-or-v1-…n3w1" and erg.nachher.limit == 5.0
    assert neu not in repr(erg)                     # Klartext nicht im repr (Logs)
    post = [a for a in t.anfragen if a["methode"] == "POST"][0]
    assert post["body"] == {"name": "memoria", "limit": 5}


def test_key_anlegen_ohne_name_oder_provisioning():
    o, t = _verwaltung({})
    assert not o.key_anlegen("  ", 5, bestaetigt=True).ok
    ohne = an.OpenRouter(api_keys={}, provisioning="", transport=t)
    assert "Provisioning" in ohne.key_anlegen("x", 5, bestaetigt=True).fehler
    assert "Provisioning" in ohne.limit_aendern("h1", 5, bestaetigt=True).fehler
    assert t.anfragen == []


# -- Anthropic --------------------------------------------------------------------

JETZT = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)


def test_anthropic_ohne_admin_key_nur_api_keys():
    a = an.Anthropic(admin_key="", transport=FakeTransport(), api_keys={"ANTHROPIC_API_KEY": "sk-ant-api03-" + "x" * 40 + "Wz9q"})
    assert not a.eingerichtet()
    [s] = a.keys()
    assert s.name == "ANTHROPIC_API_KEY" and s.kennung.endswith("Wz9q") and "x" * 20 not in s.kennung
    assert s.fehler is None and s.heute is None and an.ampel(s) == an.GRAU
    [leer] = an.Anthropic(admin_key="", transport=FakeTransport(), api_keys={}).keys()
    assert leer.fehler == "kein Key" and an.ampel(leer) == an.GRAU


def test_anthropic_kosten_report():
    def bericht(url, body):
        if "page=" not in url:
            return 200, {"data": [
                {"starting_at": "2026-10-01T00:00:00Z", "ending_at": "2026-10-02T00:00:00Z",
                 "results": [{"amount": "150.5", "currency": "USD"}, {"amount": "49.5", "currency": "USD"}]},
                {"starting_at": "2026-10-02T00:00:00Z", "ending_at": "2026-10-03T00:00:00Z", "results": []},
            ], "has_more": True, "next_page": "seite2"}
        return 200, {"data": [
            {"starting_at": "2026-10-26T00:00:00Z", "ending_at": "2026-10-27T00:00:00Z",
             "results": [{"amount": "123.0", "currency": "USD"}]},
        ], "has_more": False, "next_page": None}
    t = FakeTransport({("GET", "/organizations/cost_report"): bericht})
    [s] = an.Anthropic(admin_key=ADMIN, transport=t, jetzt=lambda: JETZT, api_keys={}).keys()
    assert s.fehler is None
    assert s.heute == pytest.approx(1.23)
    assert s.monat == pytest.approx(2.0 + 1.23)
    assert s.limit is None and not s.limit_abfragbar and an.ampel(s) == an.GRAU
    assert s.kennung == "…AdM1"
    erste = t.anfragen[0]
    assert erste["header"]["x-api-key"] == ADMIN and erste["header"]["anthropic-version"]
    assert "starting_at=2026-10-01T00%3A00%3A00Z" in erste["url"] and "bucket_width=1d" in erste["url"]
    assert "page=seite2" in t.anfragen[1]["url"]


def test_anthropic_fehler():
    t = FakeTransport({("GET", "/organizations/cost_report"): (401, {"error": {"message": "bad"}})})
    [s] = an.Anthropic(admin_key=ADMIN, transport=t, jetzt=lambda: JETZT, api_keys={}).keys()
    assert "401" in s.fehler and ADMIN not in s.fehler


def test_anthropic_admin_key_aus_umgebung(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", ADMIN)
    assert an.Anthropic(transport=FakeTransport()).eingerichtet()
