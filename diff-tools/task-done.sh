#!/usr/bin/env bash
# Mark a task complete and optionally verify output exists
# Usage: task-done.sh <analyze|summarize|write> <V1> <V2> <TASK_ID> [OUTPUT_PATH]
set -euo pipefail
cd "$(dirname "$0")/.."

PHASE="$1"; V1="$2"; V2="$3"; TASK_ID="$4"; OUTPUT="${5:-}"

if [[ -n "$OUTPUT" && ! -f "$OUTPUT" ]]; then
    echo "{\"error\": \"Output file not found: $OUTPUT\"}"
    exit 1
fi

python3 "diff-tools/${PHASE}_tasks.py" done "$V1" "$V2" "$TASK_ID"
