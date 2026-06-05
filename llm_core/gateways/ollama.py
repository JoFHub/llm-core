"""Ollama Gateway — nur stdlib, keine externe Abhängigkeit."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import LLMGateway
from .._message_utils import tool_to_openai


class OllamaGateway(LLMGateway):
    def __init__(self, host: str = "http://localhost:11434") -> None:
        self._host = host.rstrip("/")

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
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self._host}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama nicht erreichbar ({self._host}): {e}") from e

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
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self._host}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama nicht erreichbar ({self._host}): {e}") from e

        return result["message"]["content"]

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
        from .._message_utils import to_openai_messages
        from ..agent import ToolCall

        payload = {
            "model": model_name,
            "messages": [{"role": "system", "content": system}] + to_openai_messages(messages),
            "stream": False,
            "tools": [tool_to_openai(t) for t in tools],
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self._host}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama nicht erreichbar ({self._host}): {e}") from e

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
