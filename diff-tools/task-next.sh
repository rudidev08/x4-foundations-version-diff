#!/usr/bin/env bash
# Get next task for a phase/version pair
# Usage: task-next.sh <analyze|summarize|write> <V1> <V2>
set -euo pipefail
cd "$(dirname "$0")/.."

PHASE="$1"; V1="$2"; V2="$3"
python3 "diff-tools/${PHASE}_tasks.py" next "$V1" "$V2"
