# Auftrag: Kosten und Keys an einer Stelle — Konsole und Seitenleisten-Baustein in llm-core

Stand: 2026-10-26. Für eine Claude-Code-Sitzung (Cloud) im Repo `JoFHub/llm-core`. Entscheidungen des Nutzers sind
markiert; alles andere ist Vorschlag und darf begründet abweichen.

## Ziel

Eine Stelle, an der der Nutzer Verbrauch, Limits und Keys aller Sprachmodell-Zugänge sieht und verwaltet, und ein
kleiner, einheitlicher Baustein, mit dem jede App (Probatio, Memoria, Fabula) ihre Kosten in der Seitenleiste zeigt.

Anlass: ein Budget-Vorfall bei Mistral (Verbrauch unbemerkt hoch). LLM-Zugriffe sollen sicher und transparent
verwaltet werden; llm-core ist seit 2026-10-26 die einzige Quelle für den LLM-Zugriff aller Apps (vorher eingebettete
Kopie in Probatio).

## Ausgangslage

- `llm_core/cost_tracker.py`: SQLite je Rechner (`LLM_CORE_DATA_DIR`, Standard `~/.llm_core/costs.sqlite`), Spalte
  `tag` (frei wählbare Zuordnung, z. B. Kundenprofil), `set_tag_source()`, `model_summary/total_cost(tag=…)`,
  `tag_summary()`. Kosten sind **Schätzungen** aus der Preistabelle, bei OpenRouter der echte Preis je Aufruf.
- Die Daten liegen verteilt: Omen (Probatio und Fabula teilen sich `~/.llm_core`), Memoria (`~/.memoria`), Server
  (Docker, je Kunde eine Instanz), Vorführ-Laptop. Eine lokale Datenbank zeigt nie alles.
- Der **echte Verbrauch** steht beim Anbieter, je Key. Regel: ein OpenRouter-Key je App bzw. Kundenprofil, jeder mit
  Limit (in Probatio z. B. `OPENROUTER_API_KEY_VISIBILIA`, Limit 10 $). Probatio fragt heute schon
  `GET https://openrouter.ai/api/v1/key` ab (Verbrauch, Limit, heute, Monat) — Code im Anhang (das Repo Probatio
  ist privat und für diese Sitzung nicht nötig).

## Entscheidungen (Nutzer, 2026-10-26)

1. **Verwaltungs-Key ja:** Ein OpenRouter-Provisioning-Key (Verwaltung aller Keys des Kontos) wird genutzt.
2. **Konsole erstmal nur auf dem Omen** (Windows, lokal). Später eventuell auf dem eigenen Server hinter der
   Anmeldung — das Design soll das nicht verbauen (kein Windows-Only, keine festen Pfade).
3. **Mistral ist obsolet** (nicht mehr einbauen). **Anthropic (Claude-API) offenhalten:** Memoria nutzt sie noch.
   Anbieter als austauschbare Adapter; OpenRouter jetzt, Anthropic als zweiter Adapter vorbereitet (Admin-API für
   Verbrauch und Kosten — Endpunkte und Felder in der offiziellen Doku prüfen, nicht raten).

## Umfang

### 1. Anbieter-Status (ohne Oberfläche, Kern, testbar)

Neues Modul, z. B. `llm_core/anbieter.py` (Name frei):

- Einheitliches Ergebnis je Key: Anbieter, Name/Label, Key-Kennung (nur gekürzt, nie der Key), Verbrauch gesamt,
  heute, Monat, Limit, Rest, Stand (Zeitpunkt), Fehler.
- **OpenRouter:** eigener Key (`/api/v1/key`), mit Provisioning-Key alle Keys (`/api/v1/keys`, Liste mit
  Verbrauch/Limit/Status), Guthaben des Kontos (`/api/v1/credits`). Verwaltung über den Provisioning-Key: Limit
  ändern, Key deaktivieren/aktivieren, neuen Key anlegen (Name, Limit). Endpunkte und Felder gegen die aktuelle
  OpenRouter-Doku prüfen.
- **Anthropic:** Adapter-Schnittstelle und Stub; Umsetzung nur, wenn die Admin-API sauber dokumentiert ist, sonst
  als „nicht eingerichtet“ anzeigen.
- Kurzer Zwischenspeicher (z. B. 60 s), Timeout, Fehler werden zurückgegeben statt geworfen.
- **Ampel:** grün < 80 % des Limits, gelb ≥ 80 %, rot ≥ 100 % oder Key ohne Limit (ohne Limit = Risiko, siehe
  Anlass). Schwellen einstellbar.

### 2. Seitenleisten-Baustein für Streamlit-Apps

`llm_core/ui.py` (oder Unterpaket), **optionale Abhängigkeit** `llm-core[ui]` mit `streamlit` — llm-core selbst
darf nicht von Streamlit abhängen (Import nur im UI-Modul).

- Ein Aufruf, z. B. `kosten_seitenleiste(key_env="OPENROUTER_API_KEY_VISIBILIA", tag="visibilia", titel=…)`: eine
  bis drei kurze Zeilen — Verbrauch von Limit mit Ampel, heute, bei Fehler/ohne Key der lokale Verbrauch der letzten
  30 Tage aus der Kosten-DB (`tag`-Filter). Kein großer Kasten; Stil neutral, passt in Probatio, Memoria, Fabula.
- Kein API-Key verlässt den Server-Prozess, nichts davon im HTML.

### 3. Konsole (Verwaltung)

Streamlit-App im Paket, Start `python -m llm_core.konsole` (Port z. B. 8510, `localhost`), später über den Launcher
Porta des Nutzers.

- **Übersicht:** alle Keys (über den Provisioning-Key) mit Verbrauch heute/Monat/gesamt, Limit, Ampel; Guthaben des
  Kontos; Hinweis auf Keys ohne Limit.
- **Verwaltung:** Limit ändern, Key deaktivieren/aktivieren, neuen Key anlegen. **Jede Änderung mit Bestätigung**
  und Anzeige vorher/nachher. Der Klartext eines neu angelegten Keys wird genau einmal angezeigt (mit Hinweis, ihn
  sofort in die `.env` der App bzw. den Passwortmanager zu übernehmen), nie gespeichert oder geloggt.
- **Details lokal:** aus der Kosten-DB dieses Rechners — Kosten je Tag, je Modell, je `tag`; wählbarer Zeitraum;
  wählbare DB (mehrere `LLM_CORE_DATA_DIR`, z. B. `~/.llm_core` und `~/.memoria`), weil die Apps getrennte Ordner
  haben.
- Ohne Provisioning-Key: nur die Keys aus der Umgebung (`OPENROUTER_API_KEY*`) und die lokalen Details.

### 4. Doku, Tests

- README-Abschnitt: Konsole, Baustein, Einrichtung des Provisioning-Keys, Ampel.
- Tests ohne Netz: Anbieter-Antworten als Fixtures (gemockte HTTP-Antworten), Ampel-Grenzen, Fehlerfälle (401,
  402, Timeout), Zwischenspeicher, Verwaltung (richtige Requests, Bestätigung nötig). `tests/conftest.py` isoliert
  die Kosten-DB je Test (`LLM_CORE_DATA_DIR` = `tmp_path`) — so lassen.
- Bestehende Tests bleiben grün (`pytest`, 60 Tests am 2026-10-26).

## Sicherheit

- Provisioning-Key nur über Umgebung bzw. `.env` des Omen (Name z. B. `OPENROUTER_PROVISIONING_KEY`); optional
  `keyring` (Windows-Anmeldeinformationsspeicher, wie Memoria) — nie in Git, Log, HTML, Fehlermeldung.
- Keys überall nur gekürzt anzeigen (z. B. `sk-or-v1-…a1b2`).
- Die Konsole hört nur auf `localhost`. Keine Anmeldung nötig, solange sie nur lokal läuft; Server-Betrieb später
  hinter vorhandener Anmeldung.
- Keine Testläufe gegen echte Anbieter in der Cloud (keine Keys dort); echte Prüfung macht der Nutzer auf dem Omen.

## Nicht in diesem Auftrag

- Einbau in Probatio, Memoria, Fabula (jeweils eigene Sitzung im jeweiligen Repo, danach: Seitenleiste durch den
  Baustein ersetzen, eigener `LLM_CORE_DATA_DIR` je App, auf dem Server dauerhaft im Datenordner).
- Zusammenführen der lokalen Datenbanken mehrerer Rechner.
- Mistral.

## Abgabe

- Branch und Pull Request gegen `main` mit kurzer Beschreibung, was gebaut ist, was getestet ist und was nicht.
- Version in `pyproject.toml` anheben (z. B. 0.2.0) und das Extra `ui` eintragen.
- Offene Fragen und Annahmen (z. B. zu API-Feldern) im PR auflisten.

## Anhang: heutige Kostenanzeige in Probatio (Stand 2026-10-26)

Zur Orientierung, was der Baustein später ersetzt. Key-Regel in Probatio: `OPENROUTER_API_KEY_<PROFIL>` (z. B.
`OPENROUTER_API_KEY_VISIBILIA`) hat Vorrang vor dem allgemeinen `OPENROUTER_API_KEY`; der Profilname ist auch der
`tag` in der Kosten-DB (`cost_tracker.set_tag_source(profile_name)`).

Abfrage beim Anbieter (`probatio/rag/kosten.py`):

```python
def openrouter_status(profile: str | None = None, timeout: float = 5) -> dict | None:
    """Verbrauch und Limit des OpenRouter-Keys des Profils in $: {usage, usage_daily, usage_monthly, limit,
    limit_remaining}. None ohne Key oder wenn OpenRouter nicht antwortet. Kostet nichts."""
    key = api_key("openrouter", profile)
    if not key:
        return None
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode()).get("data") or {}
    except Exception:
        return None
    return {k: data.get(k) for k in ("usage", "usage_daily", "usage_monthly", "limit", "limit_remaining")}


def local_summary(days: int = 30) -> dict[str, dict[str, dict]]:
    """Aufzeichnung dieser Installation: {profil: {anbieter: {cost, calls, last}}}; „“ = ohne Profilnamen."""
    out: dict[str, dict[str, dict]] = {}
    for row in cost_tracker.tag_summary(days):
        out.setdefault(row["tag"], {})[row["provider"]] = {
            "cost": row["cost_usd"] or 0.0, "calls": row["calls"], "last": row["last"]}
    return out
```

Seitenleiste (`probatio/utils.py`, Streamlit; Status 60 s zwischengespeichert mit `st.cache_data(ttl=60)`):

```python
def fmt_cost(amount: float, cur: str = "$") -> str:
    """„12,40 $“; unter 1 mit drei Nachkommastellen („0,012 $“ — eine Anfrage kostet oft nur 0,1 Cent),
    Beträge unter 0,1 Cent als „< 0,001 $“."""
    if 0 < amount < 0.0005:
        return f"< {fmt_number(0.001, 3)} {cur}"
    return f"{fmt_number(amount, 3 if 0 < amount < 1 else 2)} {cur}"


def sidebar_cost_block():
    """Kosten des aktiven Profils: OpenRouter-Key beim Anbieter (Verbrauch von Limit), sonst die
    Aufzeichnung dieser Installation der letzten 30 Tage."""
    with st.container(key="sidebar_cost"):
        status = openrouter_status(profile_name()) if llm_available("openrouter") else None
        if status and status.get("usage") is not None:
            limit = f" von {fmt_cost(status['limit'])}" if status.get("limit") else ""
            st.caption(f":material/payments: Kosten {profile_name()}: {fmt_cost(status['usage'])}{limit}  \n"
                       f"OpenRouter-Key · heute {fmt_cost(status.get('usage_daily') or 0)}")
            return
        local = local_summary(30).get(profile_name(), {})
        if local:
            total = " · ".join(fmt_cost(v["cost"], currency(b)) for b, v in local.items())
            st.caption(f":material/payments: Kosten {profile_name()}: {total}  \nletzte 30 Tage, dieser Rechner")
```

Darstellung heute: `Kosten visibilia: 0,412 $ von 10,00 $` / `OpenRouter-Key · heute 0,031 $` (deutsche
Zahlenformate: Komma, `$` nachgestellt). Der Baustein soll mindestens das leisten, plus Ampel.
