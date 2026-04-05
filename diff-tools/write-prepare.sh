#!/usr/bin/env bash
# Write phase: Step 1 for all pending version pairs
# Checks prerequisites, initializes write task lists
set -euo pipefail
cd "$(dirname "$0")/.."

VERSIONS_FILE="diff/_versions_to_compare.md"
if [[ ! -f "$VERSIONS_FILE" ]]; then
    echo "Error: diff/_versions_to_compare.md not found."
    exit 1
fi

total_remaining=0
pairs_prepared=0
pairs_skipped=0

while IFS= read -r pair || [[ -n "$pair" ]]; do
    [[ -z "$pair" || "$pair" == \#* ]] && continue
    V1="${pair%-*}"
    V2="${pair#*-}"

    if [[ -f "diff/$pair/_completed_write" ]]; then
        echo "[$pair] Already completed — skipping"
        ((pairs_skipped++))
        continue
    fi

    if [[ ! -f "diff/$pair/_completed_analyze" ]]; then
        echo "[$pair] Analysis not complete — skipping"
        continue
    fi

    if ls "diff/$pair/_analysis/"*--part*.md &>/dev/null; then
        if [[ ! -f "diff/$pair/_completed_summarize" ]]; then
            echo "[$pair] Has multi-part domains but summarize not complete — skipping"
            continue
        fi
    fi

    echo "[$pair] Initializing write tasks..."
    python3 diff-tools/write_tasks.py init "$V1" "$V2"

    remaining=$(python3 diff-tools/write_tasks.py status "$V1" "$V2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('remaining',0))")
    total_remaining=$((total_remaining + remaining))
    ((pairs_prepared++))
    echo "[$pair] $remaining tasks remaining"

done < "$VERSIONS_FILE"

echo ""
echo "=== Summary ==="
echo "Pairs prepared: $pairs_prepared"
echo "Pairs skipped: $pairs_skipped"
echo "Total remaining tasks: $total_remaining"
