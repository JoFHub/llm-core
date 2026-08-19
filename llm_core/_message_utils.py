"""
Konvertierung zwischen normalisiertem Nachrichtenformat und backend-spezifischen Formaten.

Normalisiertes Format (wird im LLMRunner verwendet):
  {"role": "user"|"assistant"|"system", "content": str}
  {"role": "assistant", "content": str|None, "tool_calls": [{"id": str, "name": str, "input": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Tool, ToolCall

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI-Format (OpenAI, OpenRouter, Mistral, Ollama-v1)
# ---------------------------------------------------------------------------

def tool_to_openai(tool: "Tool") -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """Normalisierte Messages → OpenAI-Format."""
    result = []
    for msg in messages:
        role = msg["role"]
        if role == "assistant" and "tool_calls" in msg:
            result.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"]),
                        },
                    }
                    for tc in msg["tool_calls"]
                ],
            })
        elif role == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": msg["content"],
            })
        else:
            result.append({"role": role, "content": msg.get("content", "")})
    return result


def from_openai_response(choice) -> tuple[str | None, list["ToolCall"]]:
    """OpenAI-Antwort → (text, tool_calls) im normalisierten Format."""
    from .agent import ToolCall

    message = choice.message
    text = message.content or None
    tool_calls = []

    for tc in message.tool_calls or []:
        # Manche Provider (u.a. Gemini via OpenRouter) liefern gelegentlich
        # einen Tool-Call-Stub ohne gueltigen Funktionsnamen (leer/None) —
        # meist ein Uebersetzungsfehler des Providers, kein echter Aufruf.
        # Wird das als echter Call behandelt, landet ein Tool-Ergebnis in der
        # History fuer einen Call, den der Provider selbst nie als gueltigen
        # Funktionsaufruf ansah -> naechster Request wird mit "produced no
        # valid function calls but is followed by tool result messages"
        # abgelehnt. Also verwerfen statt weiterreichen.
        if not tc.function or not tc.function.name:
            logger.warning(
                "Tool-Call ohne gueltigen Funktionsnamen verworfen (id=%s) — "
                "vermutlich Provider-Uebersetzungsfehler.", getattr(tc, "id", "?"),
            )
            continue
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, TypeError):
            args = {}
        tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

    return text, tool_calls


# ---------------------------------------------------------------------------
# Anthropic-Format
# ---------------------------------------------------------------------------

def tool_to_anthropic(tool: "Tool") -> dict:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Normalisierte Messages → Anthropic-Format."""
    result = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg["role"]

        if role in ("user", "system"):
            result.append({"role": role, "content": msg["content"]})
            i += 1

        elif role == "assistant":
            if "tool_calls" in msg:
                content = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    })
                result.append({"role": "assistant", "content": content})
            else:
                result.append({"role": "assistant", "content": msg.get("content", "")})
            i += 1

        elif role == "tool":
            # Aufeinanderfolgende Tool-Ergebnisse → eine User-Nachricht
            tool_results = []
            while i < len(messages) and messages[i]["role"] == "tool":
                t = messages[i]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": t["tool_call_id"],
                    "content": t["content"],
                })
                i += 1
            result.append({"role": "user", "content": tool_results})

        else:
            i += 1

    return result


def from_anthropic_response(content_blocks) -> tuple[str | None, list["ToolCall"]]:
    """Anthropic-Antwort → (text, tool_calls) im normalisierten Format."""
    from .agent import ToolCall

    text = None
    tool_calls = []
    for block in content_blocks:
        if hasattr(block, "text"):
            text = block.text
        elif hasattr(block, "type") and block.type == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

    return text, tool_calls
