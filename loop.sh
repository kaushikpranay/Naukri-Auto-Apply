#!/usr/bin/env bash

MAX_ATTEMPTS=10

# Initialize git repo with an initial commit if one doesn't exist
if [ ! -d .git ]; then
    git init
    git add -A
    git commit -m "initial commit"
fi

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    echo ""
    echo "=== Attempt $attempt / $MAX_ATTEMPTS ==="

    bash watchdog.sh

    # SUCCESS: pipeline ran to completion and exited cleanly
    # Condition: run.log contains EXIT_CODE=0 AND the "DAILY RUN SUMMARY" banner
    if grep -q "EXIT_CODE=0" run.log && grep -q "DAILY RUN SUMMARY" run.log; then
        echo "FIXED"
        exit 0
    fi

    echo "Attempt $attempt did not succeed. Asking Claude to fix..."

    cat run.log | claude -p "Fix the error or STUCK issue in this log. Make the smallest possible change." --dangerously-skip-permissions

    git add -A && git commit -m "attempt $attempt" || true
done

echo "NEEDS HUMAN"
exit 1
