"""Anmeldung für MCP-Konnektoren (llm_core.mcp_anmeldung): der ganze OAuth-Weg über HTTP wie bei
claude.ai (DCR, PKCE, Anmeldeseite, Token, /mcp), Ablauf, Rotation, Widerruf, Sperre bei Fehlversuchen."""
import asyncio
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from llm_core import mcp_anmeldung as ma  # noqa: E402

URL = "http://127.0.0.1:8599"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
PASSWORT = "richtig-und-lang-genug"


class Uhr:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def aufbau(tmp_path):
    uhr = Uhr()
    zustand = {"aktiv": True}
    hash_datei = tmp_path / "passwort.hash"
    hash_datei.write_text(ma.scrypt_hash(PASSWORT), encoding="utf-8")
    anmeldung = ma.Anmeldung(tmp_path / "ablage", URL, ma.scrypt_pruefer(hash_datei), titel="Test freigeben",
                             scope="test", subject="test", aktiv=lambda: zustand["aktiv"], bremse=0, uhr=uhr)
    mcp = MCPServer(name="Test", auth_server_provider=anmeldung, auth=anmeldung.auth_settings())
    anmeldung.seiten_einbinden(mcp)

    @mcp.tool()
    def ping() -> str:
        return "pong"

    app = mcp.streamable_http_app(transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*"], allowed_origins=[]))
    with TestClient(app, base_url=URL) as client:
        yield client, anmeldung, uhr, zustand


def _registrieren(client) -> str:
    r = client.post("/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                       "grant_types": ["authorization_code", "refresh_token"],
                                       "response_types": ["code"], "client_name": "Claude"})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _anfrage(client, client_id) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = client.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
                                         "code_challenge": challenge, "code_challenge_method": "S256",
                                         "state": "abc"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    ziel = urlparse(r.headers["location"])
    assert ziel.path == "/anmelden"
    return parse_qs(ziel.query)["anfrage"][0], verifier


def _anmelden(client, anfrage, passwort=PASSWORT):
    return client.post("/anmelden", data={"anfrage": anfrage, "passwort": passwort}, follow_redirects=False)


def _tokens(client) -> tuple[str, dict]:
    client_id = _registrieren(client)
    anfrage, verifier = _anfrage(client, client_id)
    r = _anmelden(client, anfrage)
    assert r.status_code == 302, r.text
    zurueck = parse_qs(urlparse(r.headers["location"]).query)
    assert zurueck["state"] == ["abc"]
    r = client.post("/token", data={"grant_type": "authorization_code", "code": zurueck["code"][0],
                                    "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier})
    assert r.status_code == 200, r.text
    return client_id, r.json()


def _mcp(client, token):
    return client.post("/mcp", headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "t", "version": "1"}}})


def test_ganzer_weg_bis_mcp(aufbau):
    client, anmeldung, _, _ = aufbau
    assert client.post("/mcp", json={}).status_code == 401
    _, tok = _tokens(client)
    assert tok["expires_in"] == ma.ZUGANG_DAUER and tok["refresh_token"]
    assert _mcp(client, tok["access_token"]).status_code == 200
    gespeichert = json.loads(anmeldung.datei.read_text(encoding="utf-8"))
    assert tok["access_token"] in gespeichert["zugang"]
    assert len(anmeldung.angemeldet()) == 1


def test_code_nur_mit_passender_pkce(aufbau):
    client, _, _, _ = aufbau
    client_id = _registrieren(client)
    anfrage, _ = _anfrage(client, client_id)
    code = parse_qs(urlparse(_anmelden(client, anfrage).headers["location"]).query)["code"][0]
    r = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                    "client_id": client_id, "code_verifier": "falsch" * 8})
    assert r.status_code == 400


def test_zugang_laeuft_nach_einer_stunde_ab_und_erneuern_rotiert(aufbau):
    client, _, uhr, _ = aufbau
    client_id, tok = _tokens(client)
    uhr.t += ma.ZUGANG_DAUER + 1
    assert _mcp(client, tok["access_token"]).status_code == 401
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": client_id})
    assert r.status_code == 200, r.text
    neu = r.json()
    assert _mcp(client, neu["access_token"]).status_code == 200
    # Das alte Erneuerungs-Token gilt nach der Rotation nicht mehr
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": client_id})
    assert r.status_code == 400


def test_erneuern_laeuft_nach_90_tagen_ab(aufbau):
    client, _, uhr, _ = aufbau
    client_id, tok = _tokens(client)
    uhr.t += ma.ERNEUERN_DAUER + 1
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                    "client_id": client_id})
    assert r.status_code == 400


def test_revoke_widerruft_beide(aufbau):
    client, anmeldung, _, _ = aufbau
    _, tok = _tokens(client)
    # Über /revoke verlangt das SDK (mcp 2.3) auch bei öffentlichen Clients ein client_secret;
    # geprüft wird hier, was der Provider beim Widerruf tut
    zugang = asyncio.run(anmeldung.load_access_token(tok["access_token"]))
    asyncio.run(anmeldung.revoke_token(zugang))
    assert _mcp(client, tok["access_token"]).status_code == 401
    assert anmeldung.angemeldet() == []


def test_datei_loeschen_meldet_sofort_ab(aufbau):
    client, anmeldung, _, _ = aufbau
    _, tok = _tokens(client)
    anmeldung.datei.unlink()
    assert _mcp(client, tok["access_token"]).status_code == 401


def test_alle_widerrufen(aufbau):
    client, anmeldung, _, _ = aufbau
    _, tok1 = _tokens(client)
    _, tok2 = _tokens(client)
    assert anmeldung.alle_widerrufen() == 2
    assert not anmeldung.datei.exists()
    for tok in (tok1, tok2):
        assert _mcp(client, tok["access_token"]).status_code == 401


def test_schalter_aus_sperrt_tokens_und_anmeldung(aufbau):
    client, _, _, zustand = aufbau
    _, tok = _tokens(client)
    client_id = _registrieren(client)
    anfrage, _ = _anfrage(client, client_id)
    zustand["aktiv"] = False
    assert _mcp(client, tok["access_token"]).status_code == 401
    assert client.get("/anmelden", params={"anfrage": anfrage}).status_code == 403
    assert _anmelden(client, anfrage).status_code == 403
    zustand["aktiv"] = True
    assert _mcp(client, tok["access_token"]).status_code == 200


def test_fehlversuche_sperren_auch_das_richtige_passwort(aufbau):
    client, anmeldung, uhr, _ = aufbau
    anfrage, _ = _anfrage(client, _registrieren(client))
    for _ in range(ma.MAX_FEHLVERSUCHE - 1):
        r = _anmelden(client, anfrage, "falsch")
        assert r.status_code == 200 and "Passwort falsch" in r.text
    r = _anmelden(client, anfrage, "falsch")
    assert "Zu viele Fehlversuche" in r.text
    r = _anmelden(client, anfrage)  # richtig, aber gesperrt
    assert r.status_code == 200 and "Zu viele Fehlversuche" in r.text
    uhr.t += ma.SPERRZEIT + 1
    anfrage, _ = _anfrage(client, _registrieren(client))  # die alte Anfrage ist inzwischen abgelaufen
    assert _anmelden(client, anfrage).status_code == 302


def test_anfrage_laeuft_ab(aufbau):
    client, _, uhr, _ = aufbau
    anfrage, _ = _anfrage(client, _registrieren(client))
    uhr.t += ma.CODE_DAUER + 1
    assert client.get("/anmelden", params={"anfrage": anfrage}).status_code == 400
    assert _anmelden(client, anfrage).status_code == 400


def test_anmeldeseite_ohne_cache(aufbau):
    client, _, _, _ = aufbau
    anfrage, _ = _anfrage(client, _registrieren(client))
    r = client.get("/anmelden", params={"anfrage": anfrage})
    assert r.status_code == 200 and "Test freigeben" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/gesund").text == "ok"


def test_clients_begrenzt(aufbau):
    client, anmeldung, _, _ = aufbau
    client_id, _ = _tokens(client)  # dieser Client hat Tokens und bleibt
    for _ in range(ma.MAX_CLIENTS + 5):
        _registrieren(client)
    clients = json.loads(anmeldung.datei.read_text(encoding="utf-8"))["clients"]
    assert len(clients) == ma.MAX_CLIENTS
    assert client_id in clients


def test_scrypt():
    h = ma.scrypt_hash("geheim")
    assert h.startswith("scrypt$") and "geheim" not in h
    assert ma.scrypt_ok("geheim", h) and not ma.scrypt_ok("anders", h)
    assert not ma.scrypt_ok("geheim", "kaputt")


def test_pruefer_ohne_datei(tmp_path):
    assert ma.scrypt_pruefer(tmp_path / "fehlt.hash")("x") is False
