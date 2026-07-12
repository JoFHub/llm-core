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
print(result.steps)     # Tool-Aufrufe mit Ein-/Ausgaben
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

Die Preistabelle liegt in `llm_core/cost_tracker.py` (`pricing_table()`); unbekannte Modelle werden mit Kosten 0 erfasst.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Die Tests mocken alle Backends — es sind weder API-Keys noch ein laufender Ollama-Server nötig.

## Lizenz

[MIT](LICENSE)
