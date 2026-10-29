# Kosten-App als Container (Server, server/docker-compose.yml, README „Kosten-App auf dem Server“).
# Die Kosten-DBs der Apps kommen nur lesend als Volume von außen; Keys aus server/.env, nie im Image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Eigener Benutzer statt root; UID 1000 = erster Benutzer auf dem Server, dem die Datenordner gehören
RUN useradd --create-home --uid 1000 kosten

WORKDIR /app
COPY --chown=kosten:kosten . .
RUN pip install -e ".[app]"

USER kosten
EXPOSE 8510

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8510/gesund', timeout=4)"

# 0.0.0.0 nur im Container — erreichbar ist er allein über Caddy mit Anmeldung (kein Port nach außen).
CMD ["python", "-m", "llm_core.kosten_app", "--adresse", "0.0.0.0", "--port", "8510"]
