#!/usr/bin/env bash
set -euo pipefail

ZOOM_URL="${1:-}"
if [[ -z "$ZOOM_URL" ]]; then
    echo "Usage: ./start.sh <zoom-meeting-url>"
    echo ""
    echo "  Example:"
    echo "    ./start.sh 'https://zoom.us/j/12345678901?pwd=yourpassword'"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/src"
PID_FILE="/tmp/zoom-audio-capture.pid"

# Guard: already running?
if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Already running (PID $PID). Run ./stop.sh first."
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

# Prefer python3.13 (has the soniox v2 SDK); fall back to python3
if command -v python3.13 &>/dev/null; then
    PYTHON=python3.13
elif command -v python3 &>/dev/null; then
    PYTHON=python3
else
    echo "Error: python3 not found."
    exit 1
fi

echo "=== Zoom Audio Capture ==="
echo "Checking and installing dependencies…"

$PYTHON - <<PYEOF
import sys
sys.path.insert(0, "$SRC_DIR")
from deps import ensure_all
ensure_all()
PYEOF

echo ""
echo "Starting capture for: $ZOOM_URL"
echo ""

exec $PYTHON "$SRC_DIR/main.py" "$ZOOM_URL"
