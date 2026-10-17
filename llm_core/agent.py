"""Agent-Typen für den Tool-Use-Loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Tool:
    """
    Werkzeug-Definition — backend-agnostisch.

    parameters: JSON Schema-Objekt, z.B.:
        {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "..."}},
            "required": ["query"]
        }
    """
    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    """Ein einzelner Tool-Aufruf des LLM (intern)."""
    id: str
    name: str
    input: dict


@dataclass
class AgentStep:
    """Ein Schritt im Loop: was wurde gerufen, was kam zurück.

    iteration: Runde (0-basiert), in der das Modell den Aufruf angefordert hat."""
    tool_name: str
    tool_input: dict
    tool_result: str
    iteration: int = 0


@dataclass
class AgentResult:
    """
    Ergebnis eines vollständigen Agent-Runs.

    messages: vollständige Konversationshistorie im normalisierten Format —
              kann als prior_messages für den nächsten Call übergeben werden.
    iterations: genutzte Runden (Modellaufrufe mit Tools, inkl. Nudge-Runde;
                ohne den abschließenden Aufruf bei Erreichen von max_iterations).
    nudged: Runde 0 kam ohne Tool-Aufruf zurück und wurde per Nudge wiederholt.
    hit_max_iterations: max_iterations erreicht, Antwort aus dem
                        abschließenden Aufruf ohne Tools.
    """
    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    iterations: int = 0
    nudged: bool = False
    hit_max_iterations: bool = False


# Typ-Alias für Tool-Executor-Callbacks
ToolExecutor = Callable[[str, dict], str]
