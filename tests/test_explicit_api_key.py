"""Expliziter Key (LLMConfig.api_key) und abschaltbares load_dotenv."""
import subprocess
import sys

import pytest

from llm_core import LLMBackend, LLMConfig, LLMRunner


def test_api_key_default_is_none():
    assert LLMConfig(backend=LLMBackend.MISTRAL, model="m").api_key is None


def test_explicit_key_without_env(monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = LLMRunner(LLMConfig(backend=LLMBackend.MISTRAL, model="m", api_key="explizit"))
    assert runner.active_backend == LLMBackend.MISTRAL


def test_missing_key_still_raises(monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # Standardname der Konfiguration
    with pytest.raises(ValueError):
        LLMRunner(LLMConfig(backend=LLMBackend.MISTRAL, model="m"))


def test_no_dotenv_switch(tmp_path):
    (tmp_path / ".env").write_text("LLM_CORE_PROBE=aus_dotenv\n", encoding="utf-8")
    code = "import os, llm_core; print(os.environ.get('LLM_CORE_PROBE', 'fehlt'))"

    def run(extra):
        import os
        env = {k: v for k, v in os.environ.items() if k != "LLM_CORE_NO_DOTENV"}
        env.update(extra)
        return subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                              capture_output=True, text=True).stdout.strip()

    assert run({}) == "aus_dotenv"
    assert run({"LLM_CORE_NO_DOTENV": "1"}) == "fehlt"
