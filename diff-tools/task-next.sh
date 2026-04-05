#!/usr/bin/env bash
# Get next task(s) for a phase/version pair
# Usage: task-next.sh <analyze|summarize|write> <V1> <V2> [COUNT]
set -euo pipefail
cd "$(dirname "$0")/.."

PHASE="$1"; V1="$2"; V2="$3"; COUNT="${4:-}"
if [[ -n "$COUNT" ]]; then
    python3 "diff-tools/${PHASE}_tasks.py" next "$V1" "$V2" --count "$COUNT"
else
    python3 "diff-tools/${PHASE}_tasks.py" next "$V1" "$V2"
fi
