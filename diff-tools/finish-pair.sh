#!/usr/bin/env bash
# Finish a version pair: cleanup + create completion flag
# Usage: finish-pair.sh <analyze|summarize|write> <V1> <V2>
set -euo pipefail
cd "$(dirname "$0")/.."

PHASE="$1"; V1="$2"; V2="$3"
PAIR="$V1-$V2"

case "$PHASE" in
    analyze)
        rm -f "diff/$PAIR/_analysis/_progress.md"
        touch "diff/$PAIR/_completed_analyze"
        echo "[$PAIR] Analyze phase complete"
        ;;
    summarize)
        python3 diff-tools/summarize_tasks.py cleanup "$V1" "$V2"
        touch "diff/$PAIR/_completed_summarize"
        echo "[$PAIR] Summarize phase complete"
        ;;
    write)
        python3 diff-tools/write_tasks.py assemble "$V1" "$V2"
        python3 diff-tools/write_tasks.py cleanup "$V1" "$V2"
        touch "diff/$PAIR/_completed_write"
        echo "[$PAIR] Write phase complete — changelog at diff-results/diff-$PAIR.md"
        ;;
    *)
        echo "Unknown phase: $PHASE (use: analyze, summarize, write)"
        exit 1
        ;;
esac
