#!/usr/bin/env bash
# Startet die touchstone-Steuerzentrale (lokales Web-UI) im Browser.
#
# Installiert bei Bedarf das optionale [gui]-Extra (FastAPI/uvicorn/Jinja) und
# reicht alle Argumente an `touchstone gui` durch.
#
#   ./start-gui.sh                # Auto-Port, öffnet den Browser
#   ./start-gui.sh --port 8000    # fester Port
#   ./start-gui.sh --no-open      # ohne Browser (z. B. für Remote/curl)
#   ./start-gui.sh --runs ./runs  # anderes runs-Verzeichnis
#
# Beenden mit Ctrl-C (beendet den Server sauber).

set -euo pipefail

# Ins Repo-Verzeichnis wechseln (Ort dieses Skripts), damit es von überall startet.
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Fehler: 'uv' nicht gefunden. Installiere uv: https://docs.astral.sh/uv/" >&2
  exit 1
fi

# Hinweis, falls der Judge-Endpoint (LM Studio / mlx auf :1234) nicht läuft:
# 'judge' (bewerten) aus dem UI braucht ihn — Anschauen/Übersicht geht ohne.
if ! curl -s -o /dev/null --max-time 1 http://127.0.0.1:1234/v1/models 2>/dev/null; then
  echo "ℹ  Judge-Endpoint :1234 nicht erreichbar — Bewerten (judge) aus dem UI braucht ihn;"
  echo "   Übersicht/Ergebnisse anschauen geht auch ohne."
fi

echo "→ Starte touchstone gui  (installiert [gui]-Extra bei Bedarf) …"
# `uv run --extra gui` stellt das optionale Extra sicher und startet dann den Server.
exec uv run --extra gui touchstone gui "$@"
