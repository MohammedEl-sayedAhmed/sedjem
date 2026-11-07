#!/bin/sh
# Start Sedjem, the local transcription app, at http://127.0.0.1:8765 (settings: app/config.toml).
#   ./app.sh                     start and open a browser tab
#   ./app.sh --no-browser        start only
#   ./app.sh --desktop           open it in its own window (app/desktop.py)
#   ./app.sh --install-launcher  add it to the desktop's application menu
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python -m app "$@"
