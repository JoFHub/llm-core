# llm-core

Zentralisierter LLM-Zugriff für Python-Projekte: **ein** Client, **fünf** Backends — Ollama (lokal), Anthropic Claude, OpenRouter, Mistral und OpenAI. Mit Retry, JSON-Reparatur, Tool-Use-Agent-Loop, Privacy-Enforcement und automatischem Kosten-Tracking.

```python
from llm_core import LLMRunner, LLMConfig

runner = LLMRunner(LLMConfig.from_env())
antwort = runner.call_chat(system="Du bist ein hilfreicher Assistent.",
                           user="Erkläre Embeddings in einem Satz.")
```

---

## Features

- **Backend-agnostisch** — Backend-Wechsel per Konfiguration, ohne Code-Änderung
- **Retry mit Backoff** — konfigurierbar über `RetryConfig`
- **JSON-Modus mit Reparatur** — `call_single()` extrahiert und repariert auch abgeschnittene/ummantelte JSON-Antworten
- **Agent-Loop (ReAct)** — `call_agent()` mit backend-agnostischen Tool-Definitionen
- **Vision** — `call_multimodal()` für Bild-Eingaben
- **Strukturierter Output** — `call_structured()` mit Pydantic-Modellen (Anthropic)
- **Privacy Mode** — erzwingt lokale Ausführung, Cloud-Calls werden hart verhindert
- **Kosten-Tracking** — jeder API-Call wird automatisch mit Token-Zahlen und USD-Kosten in einer lokalen SQLite-Datenbank protokolliert

## Installation

```bash
pip install "llm-core @ git+https://github.com/JoFHub/llm-core.git"
```

Für die Entwicklung:

```bash
git clone https://github.com/JoFHub/llm-core.git
pip install -e "llm-core[dev]"
```

Die SDKs der Cloud-Backends (`anthropic`, `openai`) werden mitinstalliert; für das Ollama-Backend genügt ein laufender [Ollama](https://ollama.com)-Server — es wird kein zusätzliches SDK benötigt.

## Konfiguration

Konfiguration wahlweise per Umgebungsvariablen (`LLMConfig.from_env()`), Dict (`LLMConfig.from_dict()`, z. B. aus einer JSON-Datei) oder direkt im Code. Vorlage: [.env.example](.env.example).

| Variable | Bedeutung | Standard |
|---|---|---|
| `LLM_BACKEND` | `ollama` \| `anthropic` \| `openrouter` \| `mistral` \| `openai` | `ollama` |
| `LLM_MODEL` | Modell-ID | Backend-spezifischer Default |
| `LLM_TEMPERATURE` | Sampling-Temperatur | `0.1` |
| `LLM_MAX_TOKENS` | Max. Antwort-Token | `4096` |
| `LLM_PRIVACY_MODE` | `true` → nur Ollama erlaubt | `false` |
| `OLLAMA_HOST` | Ollama-Server | `http://localhost:11434` |
| `LLM_CORE_DATA_DIR` | Ablage der Kosten-Datenbank | `~/.llm_core` |

### Backends und API-Keys

| Backend | Region | API-Key-Variable | Default-Modell |
|---|---|---|---|
| `ollama` | lokal | — | `llama3.2:3b` |
| `anthropic` | US | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| `openrouter` | US (Aggregator) | `OPENROUTER_API_KEY` | `google/gemini-2.5-flash` |
| `mistral` | EU | `MISTRAL_API_KEY` | `mistral-small-latest` |
| `openai` | US | `OPENAI_API_KEY` | `gpt-4o-mini` |

`LLMBackend` kennt seine Region (`backend.region` → `"local"` / `"EU"` / `"US"`) — nützlich, um Datenflüsse anwendungsseitig zu steuern.

## API-Überblick

Alle Aufrufe laufen über den `LLMRunner`:

| Methode | Zweck |
|---|---|
| `call_chat(system, user)` | Einfacher Text-Chat → String |
| `call_single(system, user)` | Ein Aufruf → JSON-Dict, mit Retry und JSON-Reparatur (z. B. Metadaten-Extraktion) |
| `call_chat_with_history(system, messages)` | Chat mit vollständiger Konversationshistorie |
| `call_multimodal(system, user, images)` | Vision-Chat, `images=[{"base64": "..."}]` |
| `call_structured(system, user, output_type)` | Strukturierter Output als Pydantic-Modell (nur Anthropic) |
| `call_agent(system, user, tools, tool_executor)` | ReAct-Agent-Loop mit Tool Use (alle Backends mit Tool-Support) |

### Beispiel: JSON-Extraktion

```python
from llm_core import LLMRunner, LLMConfig, LLMBackend

config = LLMConfig(backend=LLMBackend.MISTRAL, model="mistral-small-latest")
runner = LLMRunner(config)

daten = runner.call_single(
    system="Extrahiere Absender, Datum (YYYY-MM-DD) und Betrag als JSON.",
    user=dokument_text,
)
# → {"absender": "Stadtwerke", "datum": "2026-03-01", "betrag": 128.40}
```

### Beispiel: Agent mit Tools

```python
from llm_core import LLMRunner, LLMConfig, Tool

suche = Tool(
    name="suche_dokumente",
    description="Durchsucht die Dokumentenablage semantisch.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Suchanfrage"}},
        "required": ["query"],
    },
)

def executor(tool_name: str, tool_input: dict) -> str:
    if tool_name == "suche_dokumente":
        return meine_suche(tool_input["query"])
    return "Unbekanntes Tool"

runner = LLMRunner(LLMConfig.from_env())
result = runner.call_agent(
    system="Beantworte Fragen mithilfe der Dokumentensuche.",
    user="Was habe ich 2024 an Strom gezahlt?",
    tools=[suche],
    tool_executor=executor,
)
print(result.answer)    # finale Antwort
print(result.steps)     # Tool-Aufrufe mit Ein-/Ausgaben und Runde (step.iteration)
print(result.iterations, result.nudged, result.hit_max_iterations)  # Runden, Nudge, Grenze erreicht
# result.messages → als prior_messages für den nächsten Turn übergeben
```

## Privacy Mode

Für sensible Inhalte lässt sich lokale Ausführung erzwingen — auf zwei Ebenen:

```python
from llm_core import with_privacy

# Config-Ebene: Kopie der Config, umgestellt auf Ollama
local_config = with_privacy(config, local_model="qwen2.5:14b-instruct-q4_K_M")

# Runner-Ebene: privacy_mode=True mit Cloud-Backend → ValueError beim Konstruieren
```

Ist `privacy_mode=True` gesetzt, verweigert der `LLMRunner` jedes Nicht-Ollama-Backend hart. Ein Cloud-Call kann so auch durch Konfigurationsfehler nicht passieren.

## Kosten-Tracking

Jeder Cloud-Call wird automatisch protokolliert (SQLite unter `~/.llm_core/costs.sqlite`, Pfad via `LLM_CORE_DATA_DIR` änderbar):

```python
from llm_core import daily_summary, model_summary, total_cost

total_cost(days=30)      # Gesamtkosten in USD
daily_summary(days=30)   # Kosten pro Tag
model_summary(days=30)   # Kosten pro Modell
```

**Zuordnung je Aufruf (`tag`):** Jeder Eintrag kann einer frei wählbaren Zuordnung gehören, z. B. dem Kundenprofil
(Probatio) oder dem Aufrufer (Agent, RAG, Hintergrund). Die App setzt einmal eine Funktion, die beim Speichern die
aktuelle Zuordnung liefert; Summen lassen sich danach filtern. Ältere Datenbanken bekommen die Spalte beim ersten
Zugriff (alte Zeilen: `""`).

```python
from llm_core import cost_tracker

cost_tracker.set_tag_source(lambda: aktives_profil())   # ohne Angabe: ""
cost_tracker.total_cost(days=30, tag="visibilia")
cost_tracker.model_summary(days=30, tag="visibilia")
cost_tracker.tag_summary(days=30)    # [{tag, provider, cost_usd, calls, last}]
```

Die Preistabelle liegt in `llm_core/cost_tracker.py` (`pricing_table()`); unbekannte Modelle werden mit Kosten 0 erfasst.

## Kosten und Keys: Kosten-App und Seitenleiste

Der **echte Verbrauch** steht beim Anbieter, je Key; die lokale Kosten-DB ist eine Schätzung je Rechner.
`llm_core.anbieter` fragt den Anbieter ab, `llm_core.ui` zeigt das Ergebnis in einer App-Seitenleiste,
`llm_core.kosten_app` ist die kleine Web-App fürs iPhone und iPad (Übersicht in Euro, einfache Verwaltung).
Mistral ist bewusst nicht dabei.

```bash
pip install "llm-core[ui] @ git+https://github.com/JoFHub/llm-core.git"    # Seitenleiste: Streamlit nur mit Extra ui
pip install "llm-core[app] @ git+https://github.com/JoFHub/llm-core.git"   # Kosten-App: Starlette, Jinja2, Uvicorn
```

### Anbieter-Status (ohne Oberfläche)

```python
import os
from llm_core import anbieter

o = anbieter.OpenRouter()                         # Keys aus Umgebung/.env, Provisioning-Key falls vorhanden
s = o.key_status(os.environ["OPENROUTER_API_KEY_VISIBILIA"], "visibilia")
s.monat, s.heute, s.limit, s.rest, s.fehler       # Fehler (401, 402, Timeout) stehen im Feld, nichts wird geworfen
anbieter.ampel(s)                                 # "gruen" | "gelb" | "rot" | "grau"
o.keys()                                          # mit Provisioning-Key alle Keys des Kontos, sonst OPENROUTER_API_KEY*
o.konto()                                         # Guthaben (nur mit Provisioning-Key)
anbieter.Anthropic().keys()                       # API-Keys (gekürzt); mit Admin-Key auch Kosten der Organisation
```

Abfragen werden 60 s zwischengespeichert (`anbieter.cache_leeren()`), Timeout 10 s. Keys erscheinen nur gekürzt
(`sk-or-v1-…a1b2`), auch in Fehlermeldungen.

**Ampel:** grün unter 80 % des Limits, gelb ab 80 %, rot ab 100 % **oder ohne Limit** (ein Key ohne Limit ist das
Risiko, das zum Budget-Vorfall geführt hat). Bei Limits mit Zurücksetzen (täglich/wöchentlich/monatlich) zählt der
Verbrauch der laufenden Periode (`limit - rest`). Grau: Fehler, oder der Anbieter liefert kein Limit (Anthropic).
Schwellen: `anbieter.Schwellen(gelb=0.8, rot=1.0)` oder `LLM_CORE_AMPEL_GELB` / `LLM_CORE_AMPEL_ROT`.

### Seitenleisten-Baustein

```python
from llm_core.ui import kosten_seitenleiste

kosten_seitenleiste(key_env="OPENROUTER_API_KEY_VISIBILIA", titel="Kosten Visibilia")
```

Ein bis zwei kleine Zeilen: `:green[:material/check_circle:] 4,12 $ von 10,00 $ (41 %)`
(Ampel als farbiges Material-Symbol); heute/Monat stehen nur in der Kosten-App. Gezeigt werden
die Kosten je Key, wie der Anbieter sie abrechnet (über alle Rechner); ohne Key oder bei Fehler eine graue Zeile
(`kein Key (…)` bzw. die Fehlermeldung), keine Schätzung aus der Kosten-DB. Der Key bleibt im
Server-Prozess; ins HTML kommt nur der Text. `kosten_zeilen(...)` liefert dieselben Zeilen ohne Streamlit.

**Apps direkt bei Anthropic** (Fabula seit 2026-10-29): Anthropic liefert je Key weder Verbrauch noch Limit. Dafür
der Baustein mit berechneten Kosten aus der Kosten-DB dieses Rechners (Token-Zahlen von Anthropic mal Preisliste in
`cost_tracker._PRICING` -- genau, solange die Preisliste stimmt):

```python
from llm_core.ui import kosten_seitenleiste_berechnet

kosten_seitenleiste_berechnet(provider="anthropic", titel="Kosten Fabula", tag="fabula")
# heute 0,45 $ · Oktober 2,74 $
```

`berechnet_zeilen(...)` liefert dieselben Zeilen ohne Streamlit. Kein Limit, keine Ampel: die Obergrenze setzt man
in der Anthropic Console.

#### Regel für alle Apps (Probatio, Memoria, Fabula)

Kosten zeigt jede App gleich, und nur so — maßgeblich sind die **Kosten je Key** beim Anbieter, keine Schätzung
(Ausnahme: Apps direkt bei Anthropic, s. oben):

1. **Ein OpenRouter-Key je App bzw. Kundenprofil, immer mit Limit** (`OPENROUTER_API_KEY_<APP|PROFIL>`, angelegt
   und begrenzt in der Kosten-App). Ohne eigenen Key gilt der allgemeine Key der App. Eine App direkt bei Anthropic
   (Fabula) hat stattdessen eine Ausgabengrenze in der Anthropic Console.
2. **In der App nur der Baustein**: einmal unten in der Seitenleiste
   `kosten_seitenleiste(key_env=<Key der App bzw. des Profils>, titel="Kosten <Name>")`, bei Anthropic
   `kosten_seitenleiste_berechnet(provider="anthropic", titel="Kosten <Name>", tag=<tag>)`.
   Keine eigene Kostenseite, keine eigene Abfrage beim Anbieter, keine eigenen Betragsformate. Der Baustein zeigt
   nur den eigenen Key — ein Kunde sieht in einer Vorführung keine anderen Kunden.
3. **Kosten-DB nur für Einzelheiten**: jeder Aufruf mit `tag` (App bzw. Kundenprofil, `cost_tracker.set_tag_source`)
   in eine eigene Kosten-DB je App (`LLM_CORE_DATA_DIR`; auf dem Server im Datenordner). Daraus berechnet die
   Kosten-App die Anthropic-Kosten; in der App erscheint sie nur über `kosten_seitenleiste_berechnet` (Anthropic).
4. **Übersicht und Verwaltung in der Kosten-App** (auf dem Server, auch am iPhone): Verbrauch aller Keys in Euro,
   Limits ändern, Keys anlegen oder sperren. Verläufe und Kosten je Tag oder Modell in den Konsolen der Anbieter.

### Kosten-App

Eine kleine Web-App, mit der man auf dem iPhone oder iPad in wenigen Sekunden sieht, was die Sprachmodelle kosten —
**nur in Euro** —, und mit der man einfache Verwaltung erledigt (nur OpenRouter). Ersetzt seit 0.3.0 die
Streamlit-Konsole.

```bash
pip install -e ".[app]"
python -m llm_core.kosten_app          # http://localhost:8510 (--port, --adresse; LLM_CORE_APP_PORT/_ADRESSE)
```

- **Übersicht:** OpenRouter (heute, laufender Monat = Summe der Keys, Rest des Guthabens), Anthropic (heute und
  Monat aus den Kosten-DBs, Zeitzone Europe/Berlin), darunter eine Zeile je OpenRouter-Key mit Ampel, Verbrauch im
  laufenden Limit-Zeitraum und Anteil am Limit; Probleme zuerst, gesperrte Keys grau am Ende. Hinweise erscheinen
  nur bei Problemen: Key ohne Limit, Limit erreicht (rot), fast erreicht (orange, `LLM_CORE_AMPEL_GELB`), Anbieter
  nicht erreichbar, kein Kurs (grau). ⟳ leert den Zwischenspeicher. Links zu den Konsolen der Anbieter.
- **Key** (Antippen): Kennung gekürzt, Status, heute/Monat/gesamt, Limit mit Zurücksetzen; mit Provisioning-Key
  **Limit ändern** (Eingabe in €, höchstens 1000 €), **sperren/entsperren**, über **+** einen **neuen Key**
  anlegen. Jede Änderung zeigt vorher → nachher und läuft erst nach der Bestätigung. Der Klartext eines neuen Keys
  erscheint genau einmal (mit „Kopieren“) und wird nie gespeichert oder geloggt.
- **Euro:** EZB-Referenzkurs über frankfurter.dev, höchstens zweimal am Tag abgefragt, im Prozess
  zwischengespeichert; ohne Antwort gilt der letzte bekannte Kurs. Fester Kurs: `LLM_CORE_EUR_KURS=0,92`. Ohne Kurs
  zeigt die App „–“ statt Beträgen (nie Dollar) und lässt keine Limits ändern. OpenRouter führt Limits in Dollar:
  ein Limit in € wird beim Speichern zum Tageskurs umgerechnet (auf Cent).
- **Info** (Fußzeile): Herkunft der Zahlen, Kurs, Ampel, Stand.
- Kosten-DBs: `LLM_CORE_KOSTEN_DBS` (Ordner, getrennt mit `;` unter Windows bzw. `:` sonst; der alte Name
  `LLM_CORE_KONSOLE_DBS` gilt weiter), `LLM_CORE_DATA_DIR`, `~/.llm_core`, `~/.memoria`; nur lesend.

Technik: Starlette + Jinja2, serverseitig gerendert, HTML-Formulare, eigenes CSS, ein wenig JavaScript (Kopieren),
kein Build-Schritt. Logik ohne Web-Framework in `kosten_app/logik.py`, `kurs.py`, `daten.py`.

Sicherheit: keine eigene Anmeldung — lokal nur `localhost`, auf dem Server nur hinter Caddy. Keys nie im HTML, in
URLs oder Logs (nur gekürzt). Ändernde Aktionen nur per POST mit CSRF-Token (an das Sitzungs-Cookie gebunden,
`SameSite=Strict`) und Prüfung von `Origin`/`Sec-Fetch-Site`; Bestätigung serverseitig (Plan einmal verwendbar,
10 Minuten gültig). `Content-Security-Policy` ohne fremde Quellen, `Cache-Control: no-store` für alle Seiten.
`/gesund` für den Healthcheck (fragt keinen Anbieter).

#### Als App auf den Home-Bildschirm (iPhone, iPad)

1. Die Adresse (Server: `https://kosten.31-70-152-107.sslip.io`) in **Safari** öffnen und anmelden.
2. Teilen-Knopf → **Zum Home-Bildschirm** → „Kosten“.
3. Beim ersten Start der Home-Bildschirm-App einmal anmelden: iOS gibt ihr einen eigenen Cookie-Speicher, die
   Anmeldung aus Safari gilt dort nicht. Die Anmeldeseite (`/_anmeldung/`) öffnet sich in der App selbst (gleiche
   Adresse, `scope: "/"`); danach hält das Cookie 30 Tage.

### Kosten-App auf dem Server

Die App läuft auf dem Probatio-Server, damit die Kosten immer erreichbar sind: OpenRouter (Memoria, Probatio)
kommt ohnehin über die Keys beim Anbieter, und Fabula — die einzige App direkt bei Anthropic — läuft nur dort,
ihre Kosten-DB liegt also auf demselben Rechner.

- Eigener Stack in `~/llm-core/server/` (Projekt `llm-core`, Dienst `kosten:8510`, kein Port nach außen) im Netz
  `probatio`; HTTPS und Anmeldung macht Caddy aus dem Probatio-Stack
  (`{$KOSTEN_HOST} { import geschuetzt kosten kosten:8510 }`), Benutzer `kosten` (`bash passwort.sh kosten` in
  `~/probatio/server`).
- Kosten-DBs nur lesend eingebunden: Fabula (`~/fabula/server/daten/llm_core`) und die Probatio-Instanzen
  `ta`, `visibilia` (`~/probatio/server/daten/<kunde>/probatio/llm_core`), Variable `LLM_CORE_KOSTEN_DBS`.
- Keys in `server/.env` (Vorlage `server/env.example`): `KOSTEN_HOST`, `OPENROUTER_PROVISIONING_KEY`, optional
  `LLM_CORE_EUR_KURS`. Das Image enthält keine Keys (`.dockerignore`).
- Aktualisieren: `ssh probatio-server`, `bash llm-core/server/aktualisieren.sh` (holt `main`, baut, startet neu,
  prüft Gesundheit über `/gesund` und die Anmeldeseite).

Einrichten (einmal): `git clone https://github.com/JoFHub/llm-core.git ~/llm-core`, `server/.env` aus
`env.example` anlegen, `KOSTEN_HOST` auch in `~/probatio/server/.env` eintragen, Caddy im Probatio-Stack neu
starten, Passwort setzen, `bash ~/llm-core/server/aktualisieren.sh --immer`.

### Einrichtung

| Variable | Zweck |
|---|---|
| `OPENROUTER_API_KEY*` | Keys der Apps (ein Key je App bzw. Kundenprofil, jeder mit Limit) |
| `OPENROUTER_PROVISIONING_KEY` | Provisioning-/Management-Key: alle Keys, Guthaben, Verwaltung (Alias `OPENROUTER_MANAGEMENT_KEY`) |
| `LLM_CORE_EUR_KURS` | optional: fester Euro-Kurs (Euro je Dollar), sonst EZB-Kurs |
| `ANTHROPIC_ADMIN_KEY` | optional: Admin-Key (`sk-ant-admin…`) für die Kosten der Organisation; ohne: nur die API-Keys `ANTHROPIC_API_KEY*`, gekürzt und ohne Verbrauch |

1. Bei OpenRouter unter *Settings → Management Keys* (früher *Provisioning Keys*) einen Key anlegen.
2. In die `.env` des Rechners eintragen (`OPENROUTER_PROVISIONING_KEY=…`), nie in Git. Die `.env` im
   Startordner wird über `llm_core.config` geladen.
3. Alternativ im Anmeldeinformationsspeicher: `pip install "llm-core[keyring]"`, dann
   `keyring set llm-core OPENROUTER_PROVISIONING_KEY` (gilt ebenso für `ANTHROPIC_ADMIN_KEY`).

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Die Tests mocken alle Backends — es sind weder API-Keys noch ein laufender Ollama-Server nötig.

## Lizenz

[MIT](LICENSE)
