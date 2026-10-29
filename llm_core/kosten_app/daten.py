"""Kosten aus den Kosten-DBs (costs.sqlite) der Apps, nur lesend — für Anbieter ohne Abfrage je Key (Anthropic).

Auf dem Server sind die DBs der Apps nur lesend eingebunden (`LLM_CORE_KOSTEN_DBS`, server/docker-compose.yml);
lokal kommen `LLM_CORE_DATA_DIR` und die Standardordner dazu.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DB_NAME = "costs.sqlite"
STANDARD_ORDNER = ("~/.llm_core", "~/.memoria")
DBS_ENV = ("LLM_CORE_KOSTEN_DBS", "LLM_CORE_KONSOLE_DBS")   # zweiter Name: alt (Kosten-Konsole)
ZEITZONE = ZoneInfo("Europe/Berlin")


def db_kandidaten() -> list[Path]:
    """Vorhandene Kosten-DBs: LLM_CORE_KOSTEN_DBS (Ordner oder Dateien, getrennt mit os.pathsep),
    LLM_CORE_DATA_DIR und die Standardordner. Doppelte entfallen, Reihenfolge bleibt."""
    ordner: list[str] = []
    for name in DBS_ENV:
        ordner += [o for o in os.environ.get(name, "").split(os.pathsep) if o.strip()]
    if os.environ.get("LLM_CORE_DATA_DIR"):
        ordner.append(os.environ["LLM_CORE_DATA_DIR"])
    ordner += list(STANDARD_ORDNER)
    gesehen: set[Path] = set()
    ergebnis: list[Path] = []
    for o in ordner:
        pfad = Path(o.strip()).expanduser()
        if pfad.suffix != ".sqlite":
            pfad = pfad / DB_NAME
        pfad = pfad.resolve()
        if pfad not in gesehen and pfad.is_file():
            gesehen.add(pfad)
            ergebnis.append(pfad)
    return ergebnis


def _lesen(db: Path) -> sqlite3.Connection:
    # Nur lesend: eine DB wird nie angelegt oder verändert.
    return sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True)


def kosten_seit(dbs: list[Path], seit: datetime, provider: str | None = None) -> tuple[float, str | None]:
    """Summe der Kosten (USD) seit einem Zeitpunkt (mit Zeitzone) über mehrere DBs, optional nur ein Anbieter.
    Gibt (Summe, Fehler) zurück; eine nicht lesbare DB zählt nicht mit und steht im Fehlertext."""
    where, args = "timestamp >= ?", [seit.astimezone(timezone.utc).isoformat()]
    if provider is not None:
        where += " AND provider = ?"
        args.append(provider)
    summe, fehler = 0.0, []
    for db in dbs:
        try:
            conn = _lesen(db)
        except sqlite3.Error as e:
            fehler.append(f"{Path(db).parent.name}: {e}")
            continue
        try:
            if conn.execute("PRAGMA table_info(llm_usage)").fetchone() is None:
                continue
            summe += conn.execute(f"SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE {where}",
                                  args).fetchone()[0] or 0.0
        except sqlite3.Error as e:
            fehler.append(f"{Path(db).parent.name}: {e}")
        finally:
            conn.close()
    return summe, ("Kosten-DB nicht lesbar (" + "; ".join(fehler) + ")") if fehler else None


def heute_monat(dbs: list[Path], provider: str, jetzt: datetime | None = None
                ) -> tuple[float | None, float | None, str | None]:
    """Kosten (USD) heute und im laufenden Monat (Europe/Berlin): (heute, monat, Fehler).
    Ohne DB (None, None, Grund)."""
    jetzt = (jetzt or datetime.now(ZEITZONE)).astimezone(ZEITZONE)
    if not dbs:
        return None, None, "keine Kosten-DB gefunden"
    tag_beginn = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
    heute, f1 = kosten_seit(dbs, tag_beginn, provider)
    monat, f2 = kosten_seit(dbs, tag_beginn.replace(day=1), provider)
    return heute, monat, f1 or f2
