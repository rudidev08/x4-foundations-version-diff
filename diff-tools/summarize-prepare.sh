#!/usr/bin/env bash
# Summarize phase: Step 1 for all pending version pairs
# Checks prerequisites, initializes summarize task lists
set -euo pipefail
cd "$(dirname "$0")/.."

VERSIONS_FILE="diff/_versions_to_compare.md"
if [[ ! -f "$VERSIONS_FILE" ]]; then
    echo "Error: diff/_versions_to_compare.md not found. Run /diff-analyze first."
    exit 1
fi

total_remaining=0
pairs_prepared=0
pairs_skipped=0

while IFS= read -r pair || [[ -n "$pair" ]]; do
    [[ -z "$pair" || "$pair" == \#* ]] && continue
    V1="${pair%-*}"
    V2="${pair#*-}"

    if [[ -f "diff/$pair/_completed_summarize" ]]; then
        echo "[$pair] Already completed — skipping"
        ((pairs_skipped++))
        continue
    fi

    if [[ ! -f "diff/$pair/_completed_analyze" ]]; then
        echo "[$pair] Analysis not complete — skipping (run /diff-analyze first)"
        continue
    fi

    echo "[$pair] Initializing summarize tasks..."
    init_result=$(python3 diff-tools/summarize_tasks.py init "$V1" "$V2")
    echo "[$pair] $init_result"

    if echo "$init_result" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='nothing_to_summarize' else 1)" 2>/dev/null; then
        echo "[$pair] Nothing to summarize — creating flag"
        touch "diff/$pair/_completed_summarize"
        ((pairs_skipped++))
        continue
    fi

    remaining=$(python3 diff-tools/summarize_tasks.py status "$V1" "$V2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('remaining',0))")
    total_remaining=$((total_remaining + remaining))
    ((pairs_prepared++))
    echo "[$pair] $remaining tasks remaining"

done < "$VERSIONS_FILE"

echo ""
echo "=== Summary ==="
echo "Pairs prepared: $pairs_prepared"
echo "Pairs skipped: $pairs_skipped"
echo "Total remaining tasks: $total_remaining"
