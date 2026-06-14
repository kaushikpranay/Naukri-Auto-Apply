#!/usr/bin/env bash

LOG=run.log
STUCK_TIMEOUT=60
TMPOUT="/tmp/naukri_raw_$$.txt"

> "$LOG"
> "$TMPOUT"

cleanup() {
    kill "$PIPE_PID" 2>/dev/null || true
    rm -f "$TMPOUT"
}
trap cleanup EXIT

# Run python (unbuffered) into temp file so we have a stable PID to monitor/kill
python -u discover_applications.py > "$TMPOUT" 2>&1 &
PYTHON_PID=$!

# Tail raw output and prepend timestamps into run.log
tail -f "$TMPOUT" | awk '{ print strftime("[%Y-%m-%d %H:%M:%S]"), $0; fflush() }' >> "$LOG" &
PIPE_PID=$!

# Stall detection: kill python if run.log grows no new bytes for STUCK_TIMEOUT seconds
last_size=0
last_change=$(date +%s)

while kill -0 "$PYTHON_PID" 2>/dev/null; do
    sleep 5
    current_size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
    now=$(date +%s)

    if [ "$current_size" != "$last_size" ]; then
        last_size=$current_size
        last_change=$now
    else
        elapsed=$(( now - last_change ))
        if [ "$elapsed" -ge "$STUCK_TIMEOUT" ]; then
            printf '[%s] STUCK: no output for %ds\n' \
                "$(date '+%Y-%m-%d %H:%M:%S')" "$STUCK_TIMEOUT" >> "$LOG"
            kill "$PYTHON_PID" 2>/dev/null || true
            sleep 2
            kill -9 "$PYTHON_PID" 2>/dev/null || true
            echo "EXIT_CODE=124" >> "$LOG"
            exit 124
        fi
    fi
done

wait "$PYTHON_PID"
EXIT_CODE=$?

# Give tail/awk time to flush any remaining buffered lines
sleep 3

echo "EXIT_CODE=$EXIT_CODE" >> "$LOG"
exit "$EXIT_CODE"
