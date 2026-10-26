"""Start der Konsole: python -m llm_core.konsole [--port 8510] [--adresse localhost]."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

STANDARD_PORT = 8510


def befehl(port: int, adresse: str) -> list[str]:
    app = Path(__file__).with_name("app.py")
    return [sys.executable, "-m", "streamlit", "run", str(app),
            "--server.address", adresse, "--server.port", str(port),
            "--server.headless", "true", "--browser.gatherUsageStats", "false"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llm_core.konsole", description="Kosten-Konsole von llm-core")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LLM_CORE_KONSOLE_PORT", STANDARD_PORT)))
    # Nur localhost: die Konsole hat keine eigene Anmeldung. Andere Adressen nur hinter einer Anmeldung.
    parser.add_argument("--adresse", default=os.environ.get("LLM_CORE_KONSOLE_ADRESSE", "localhost"))
    args = parser.parse_args(argv)
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print('Streamlit fehlt: pip install "llm-core[ui]"', file=sys.stderr)
        return 1
    print(f"Kosten-Konsole: http://{args.adresse}:{args.port}")
    return subprocess.call(befehl(args.port, args.adresse))


if __name__ == "__main__":
    raise SystemExit(main())
