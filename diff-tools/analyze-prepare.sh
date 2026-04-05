#!/usr/bin/env bash
# Analyze phase: Steps 1-3 for all pending version pairs
# Generates diffs, prepares batches, initializes task lists
set -euo pipefail
cd "$(dirname "$0")/.."

VERSIONS_FILE="diff/_versions_to_compare.md"
if [[ ! -f "$VERSIONS_FILE" ]]; then
    echo '{"error": "diff/_versions_to_compare.md not found"}'
    exit 1
fi

total_remaining=0
pairs_prepared=0
pairs_skipped=0

while IFS= read -r pair || [[ -n "$pair" ]]; do
    [[ -z "$pair" || "$pair" == \#* ]] && continue
    V1="${pair%-*}"
    V2="${pair#*-}"

    if [[ -f "diff/$pair/_completed_analyze" ]]; then
        echo "[$pair] Already completed — skipping"
        ((pairs_skipped++))
        continue
    fi

    if [[ -d "diff/$pair" ]]; then
        echo "[$pair] Diffs already exist"
    else
        echo "[$pair] Generating diffs..."
        python3 diff-tools/version_diff.py "$V1" "$V2"
    fi

    if [[ -d "diff/$pair/_analysis/_batches" ]]; then
        echo "[$pair] Batches already exist"
    else
        echo "[$pair] Preparing batches..."
        python3 diff-tools/prepare_diff_analysis.py "$V1" "$V2"
    fi

    echo "[$pair] Initializing tasks..."
    python3 diff-tools/analyze_tasks.py init "$V1" "$V2"

    remaining=$(python3 diff-tools/analyze_tasks.py status "$V1" "$V2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('remaining',0))")
    total_remaining=$((total_remaining + remaining))
    ((pairs_prepared++))
    echo "[$pair] $remaining tasks remaining"

done < "$VERSIONS_FILE"

echo ""
echo "=== Summary ==="
echo "Pairs prepared: $pairs_prepared"
echo "Pairs skipped (already done): $pairs_skipped"
echo "Total remaining tasks: $total_remaining"
