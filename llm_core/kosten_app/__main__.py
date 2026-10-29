"""Start der Kosten-App: python -m llm_core.kosten_app [--port 8510] [--adresse localhost]."""
from __future__ import annotations

import argparse
import os
import sys

STANDARD_PORT = 8510


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llm_core.kosten_app", description="Kosten-App von llm-core")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LLM_CORE_APP_PORT", STANDARD_PORT)))
    # Nur localhost: die App hat keine eigene Anmeldung. Andere Adressen nur hinter einer Anmeldung (Server: Caddy).
    parser.add_argument("--adresse", default=os.environ.get("LLM_CORE_APP_ADRESSE", "localhost"))
    args = parser.parse_args(argv)
    try:
        import uvicorn
        from llm_core.kosten_app.web import erstelle_app
    except ImportError:
        print('Starlette/Uvicorn fehlen: pip install "llm-core[app]"', file=sys.stderr)
        return 1
    print(f"Kosten-App: http://{args.adresse}:{args.port}")
    # Zugriffsprotokoll aus: es enthielte nur Pfade, aber nichts davon wird gebraucht
    uvicorn.run(erstelle_app(), host=args.adresse, port=args.port, access_log=False, server_header=False,
                proxy_headers=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
