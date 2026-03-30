#!/usr/bin/env bash
# Stop the Zoom audio capture and clean up all associated resources.
set -euo pipefail

PID_FILE="/tmp/zoom-audio-capture.pid"
SINK_NAME="zoom_capture"
DISPLAY_NUM=":99"

# ── 1. Stop the Python process (if still running) ──────────────────────────
if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping Zoom Audio Capture (PID $PID)…"
        kill -TERM "$PID"
        for i in $(seq 1 10); do
            if ! kill -0 "$PID" 2>/dev/null; then break; fi
            sleep 1
        done
        if kill -0 "$PID" 2>/dev/null; then
            echo "Process did not exit gracefully — force killing."
            kill -KILL "$PID" 2>/dev/null || true
        fi
        echo "Python process stopped."
    else
        echo "Process $PID is not running (stale PID file)."
    fi
    rm -f "$PID_FILE"
else
    echo "No PID file found — cleaning up any orphaned resources."
fi

# ── 2. Kill any orphaned Chromium processes using our virtual display ───────
# Playwright sets DISPLAY in the environment, not as a CLI flag — check /proc.
CHROME_PIDS=""
for pid in $(pgrep -f chromium 2>/dev/null); do
    if tr '\0' '\n' < /proc/"$pid"/environ 2>/dev/null \
       | grep -q "^DISPLAY=$DISPLAY_NUM$"; then
        CHROME_PIDS="$CHROME_PIDS $pid"
    fi
done
if [[ -n "$CHROME_PIDS" ]]; then
    echo "Killing orphaned Chromium (PIDs:$CHROME_PIDS)…"
    kill -TERM $CHROME_PIDS 2>/dev/null || true
    sleep 1
    kill -KILL $CHROME_PIDS 2>/dev/null || true
fi

# ── 3. Kill Xvfb on display :99 ────────────────────────────────────────────
XVFB_PID=$(pgrep -f "Xvfb $DISPLAY_NUM" 2>/dev/null || true)
if [[ -n "$XVFB_PID" ]]; then
    echo "Killing Xvfb on $DISPLAY_NUM (PID $XVFB_PID)…"
    kill -TERM $XVFB_PID 2>/dev/null || true
    sleep 1
    kill -KILL $XVFB_PID 2>/dev/null || true
    echo "Xvfb stopped."
fi

# ── 4. Unload ALL PulseAudio null sinks matching zoom_capture* ─────────────
MODULE_IDS=$(pactl list modules short 2>/dev/null \
    | awk -v name="$SINK_NAME" '$0 ~ name {print $1}')
if [[ -n "$MODULE_IDS" ]]; then
    echo "Unloading PulseAudio sink(s) '$SINK_NAME*'…"
    while IFS= read -r mid; do
        pactl unload-module "$mid" 2>/dev/null || true
    done <<< "$MODULE_IDS"
    echo "PulseAudio sink(s) removed."
fi

# ── 5. Reset PulseAudio default sink to system default ─────────────────────
DEFAULT_SINK=$(pactl list sinks short 2>/dev/null | grep -v "$SINK_NAME" | awk 'NR==1{print $2}' || true)
if [[ -n "$DEFAULT_SINK" ]]; then
    pactl set-default-sink "$DEFAULT_SINK" 2>/dev/null || true
    echo "Default sink reset to: $DEFAULT_SINK"
fi

echo "Done."
