#!/usr/bin/env bash
# Kosten-Konsole auf dem Server auf den Stand von GitHub (main) bringen (README „Konsole auf dem Server“):
#   bash aktualisieren.sh            holen, bauen, neu starten, prüfen
#   bash aktualisieren.sh --immer    auch ohne neuen Commit bauen und neu starten
# Bricht ab, wenn auf dem Server Dateien im Repo von Hand geändert sind (git pull nur „fast-forward“).
# Keys (server/.env) bleiben unberührt.
set -euo pipefail
cd "$(dirname "$0")"
immer=0
for a in "$@"; do
    case $a in
        --immer) immer=1 ;;
        *) echo "Unbekannte Option: $a"; exit 2 ;;
    esac
done

alt=$(git rev-parse HEAD)
if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    echo "Abbruch: Dateien im Repo sind auf dem Server geändert:"; git status --short --untracked-files=no
    echo "Prüfen und mit 'git checkout <datei>' verwerfen, dann erneut starten."; exit 1
fi
git pull --ff-only -q
neu=$(git rev-parse HEAD)
if [[ $alt == "$neu" && $immer == 0 ]]; then
    echo "Schon aktuell: $(git log --oneline -1)"; exit 0
fi
[[ $alt != "$neu" ]] && { echo "Neu:"; git log --oneline "$alt..$neu"; }

echo "Baue und starte …"
docker compose up -d --build --quiet-pull 2>&1 | grep -vE "^ *(#|=>)" | tail -n 8

echo -n "Warte auf den Container "
for _ in $(seq 1 40); do
    status=$(docker compose ps --format '{{.Health}}' kosten)
    [[ $status == healthy ]] && break
    echo -n "."; sleep 3
done
echo " $status"
fehler=0
[[ $status == healthy ]] || { echo "FEHLER: Container nicht gesund."; fehler=1; }

# Von außen: ohne Anmeldung muss die Anmeldeseite kommen (302)
host=$(grep -E '^KOSTEN_HOST=' .env | cut -d= -f2)
code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "https://$host/" || true)
echo "https://$host/ → $code"
[[ $code == 302 ]] || fehler=1

docker image prune -f >/dev/null   # alte Image-Stände entfernen (Platz)
if [[ $fehler == 0 ]]; then echo "Fertig: $(git log --oneline -1)"; else echo "FEHLER — siehe oben."; exit 1; fi
