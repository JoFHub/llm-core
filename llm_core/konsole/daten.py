"""Lokale Details für die Konsole: liest eine Kosten-DB (costs.sqlite) nur lesend.

Die Apps legen ihre DB in getrennten Ordnern ab (LLM_CORE_DATA_DIR, z. B. ~/.llm_core und ~/.memoria);
die Konsole lässt eine davon wählen, statt nur die des eigenen Prozesses zu zeigen.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_NAME = "costs.sqlite"
STANDARD_ORDNER = ("~/.llm_core", "~/.memoria")


def db_kandidaten() -> list[Path]:
    """Vorhandene Kosten-DBs: LLM_CORE_KONSOLE_DBS (Ordner, getrennt mit os.pathsep),
    LLM_CORE_DATA_DIR und die Standardordner. Doppelte entfallen, Reihenfolge bleibt."""
    ordner: list[str] = []
    zusatz = os.environ.get("LLM_CORE_KONSOLE_DBS", "")
    ordner += [o for o in zusatz.split(os.pathsep) if o.strip()]
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
    # Nur lesend: eine gewählte DB wird nie angelegt oder verändert.
    return sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True)


def _seit(tage: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()


def details(db: Path, tage: int = 30, tag: str | None = None, provider: str | None = None) -> dict:
    """{'je_tag': [...], 'je_modell': [...], 'je_tag_zuordnung': [...], 'summe': float, 'fehler': str|None}.
    tag: nur diese Zuordnung (None = alle); provider: nur dieser Anbieter (None = alle)."""
    leer = {"je_tag": [], "je_modell": [], "je_tag_zuordnung": [], "summe": 0.0, "fehler": None}
    where, args = "timestamp >= ?", [_seit(tage)]
    if provider is not None:
        where += " AND provider = ?"
        args.append(provider)
    basis_where, basis_args = where, list(args)
    if tag is not None:
        where += " AND tag = ?"
        args.append(tag)
    try:
        conn = _lesen(db)
    except sqlite3.Error as e:
        return {**leer, "fehler": f"DB nicht lesbar: {e}"}
    try:
        spalten = {r[1] for r in conn.execute("PRAGMA table_info(llm_usage)")}
        if not spalten:
            return leer
        if "tag" not in spalten:  # Alt-DB ohne Zuordnung
            if tag:
                return leer
            where, args = basis_where, basis_args
            tag_ausdruck = "''"
        else:
            tag_ausdruck = "tag"
        je_tag = conn.execute(
            "SELECT substr(timestamp, 1, 10) AS tag_datum, SUM(cost_usd), COUNT(*) "
            f"FROM llm_usage WHERE {where} GROUP BY tag_datum ORDER BY tag_datum", args).fetchall()
        je_modell = conn.execute(
            "SELECT model, provider, SUM(cost_usd), COUNT(*), SUM(prompt_tokens), SUM(completion_tokens) "
            f"FROM llm_usage WHERE {where} GROUP BY model, provider ORDER BY SUM(cost_usd) DESC", args).fetchall()
        je_zuordnung = conn.execute(
            f"SELECT {tag_ausdruck} AS z, provider, SUM(cost_usd), COUNT(*), MAX(timestamp) "
            f"FROM llm_usage WHERE {where} GROUP BY z, provider ORDER BY SUM(cost_usd) DESC", args).fetchall()
    except sqlite3.Error as e:
        return {**leer, "fehler": f"DB nicht lesbar: {e}"}
    finally:
        conn.close()
    return {
        "je_tag": [{"datum": r[0], "kosten_usd": r[1] or 0.0, "aufrufe": r[2]} for r in je_tag],
        "je_modell": [{"modell": r[0], "anbieter": r[1], "kosten_usd": r[2] or 0.0, "aufrufe": r[3],
                       "prompt_tokens": r[4] or 0, "completion_tokens": r[5] or 0} for r in je_modell],
        "je_tag_zuordnung": [{"zuordnung": r[0] or "(ohne)", "anbieter": r[1], "kosten_usd": r[2] or 0.0,
                              "aufrufe": r[3], "letzter": r[4]} for r in je_zuordnung],
        "summe": sum(r[1] or 0.0 for r in je_tag),
        "fehler": None,
    }


def kosten_seit(dbs: list[Path], seit: datetime, provider: str | None = None) -> tuple[float, str | None]:
    """Summe der Kosten seit einem Zeitpunkt (mit Zeitzone) über mehrere DBs, optional nur ein Anbieter.
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
            fehler.append(f"{db}: {e}")
            continue
        try:
            if conn.execute("PRAGMA table_info(llm_usage)").fetchone() is None:
                continue
            summe += conn.execute(f"SELECT COALESCE(SUM(cost_usd), 0) FROM llm_usage WHERE {where}",
                                  args).fetchone()[0] or 0.0
        except sqlite3.Error as e:
            fehler.append(f"{db}: {e}")
        finally:
            conn.close()
    return summe, ("DB nicht lesbar: " + "; ".join(fehler)) if fehler else None
