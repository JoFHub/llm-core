"""Ollama Gateway — nur stdlib, keine externe Abhängigkeit."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .base import LLMGateway


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
