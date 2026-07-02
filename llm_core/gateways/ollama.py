"""Ollama Gateway — nur stdlib, keine externe Abhängigkeit."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import LLMGateway
from .._message_utils import tool_to_openai

# Lokale Modelle auf CPU sind langsam: Kaltladen eines 7B-Modells dauert
# 30–90 s, lange Antworten mehrere Minuten. Per Env-Var übersteuerbar.
_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "600"))
# Modell zwischen Aufrufen im Speicher halten (Ollama-Default: nur 5 m) —
# erspart periodischen Jobs (z.B. 15-Minuten-Sync) das erneute Kaltladen.
_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
# Kontextfenster: Ollamas Default (4096 Tokens) schneidet lange RAG-Prompts
# still ab — das Modell sieht dann nur einen Teil der Dokumente. 16k passt
# für 14B-q4-Modelle bequem in 16 GB VRAM (KV-Cache ~2 GB).
_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))


class OllamaGateway(LLMGateway):
    def __init__(self, host: str = "http://localhost:11434") -> None:
        self._host = host.rstrip("/")

    def _post_chat(self, payload: dict) -> dict:
        """POST an /api/chat mit aussagekräftigen Fehlermeldungen."""
        payload.setdefault("keep_alive", _KEEP_ALIVE)
        req = urllib.request.Request(
            f"{self._host}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # Server antwortet, lehnt aber ab (z.B. Modell nicht gepullt) —
            # Ollamas Fehlertext aus dem Body durchreichen statt "nicht erreichbar"
            try:
                detail = json.loads(e.read().decode()).get("error", "")
            except Exception:
                detail = ""
            raise RuntimeError(
                f"Ollama-Fehler (HTTP {e.code}): {detail or e.reason} "
                f"[Modell: {payload.get('model', '?')}]"
            ) from e
        except TimeoutError as e:
            raise RuntimeError(
                f"Ollama-Zeitüberschreitung nach {_TIMEOUT:.0f}s "
                f"(Modell: {payload.get('model', '?')}). Bei langsamer Hardware "
                f"OLLAMA_TIMEOUT erhöhen."
            ) from e
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), TimeoutError):
                raise RuntimeError(
                    f"Ollama-Zeitüberschreitung nach {_TIMEOUT:.0f}s "
                    f"(Modell: {payload.get('model', '?')}). Bei langsamer Hardware "
                    f"OLLAMA_TIMEOUT erhöhen."
                ) from e
            raise RuntimeError(f"Ollama nicht erreichbar ({self._host}): {e}") from e

    def chat(
        self,
        *,
        system: str,
        user: str,
        model_name: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> tuple[str, str]:
        payload: dict = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": _NUM_CTX},
        }
        if json_mode:
            payload["format"] = "json"

        result = self._post_chat(payload)
        return result["message"]["content"], model_name

    def chat_with_history(
        self,
        *,
        system: str,
        messages: list[dict],
        model_name: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": model_name,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": _NUM_CTX},
        }
        result = self._post_chat(payload)
        return result["message"]["content"]

    def _to_ollama_messages(self, messages: list[dict]) -> list[dict]:
        """Wie to_openai_messages, aber arguments als dict (nicht JSON-String) — Ollama-Format."""
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
                                "arguments": tc["input"],  # dict, kein json.dumps
                            },
                        }
                        for tc in msg["tool_calls"]
                    ],
                })
            elif role == "tool":
                result.append({
                    "role": "tool",
                    "content": msg["content"],
                })
            else:
                result.append({"role": role, "content": msg.get("content", "")})
        return result

    def chat_with_tools(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list,
        model_name: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str | None, list, list[dict]]:
        from ..agent import ToolCall

        payload = {
            "model": model_name,
            "messages": [{"role": "system", "content": system}] + self._to_ollama_messages(messages),
            "stream": False,
            "tools": [tool_to_openai(t) for t in tools],
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": _NUM_CTX},
        }
        result = self._post_chat(payload)
        message = result["message"]
        text = message.get("content") or None
        tool_calls = []
        for i, tc in enumerate(message.get("tool_calls") or []):
            f = tc.get("function", {})
            args = f.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", f"ollama_call_{i}"),
                name=f.get("name", ""),
                input=args,
            ))

        assistant_msg: dict = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "input": tc.input} for tc in tool_calls
            ]

        return text, tool_calls, messages + [assistant_msg]
