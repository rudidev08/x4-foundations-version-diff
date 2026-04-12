#!/usr/bin/env bash
# Prepare all version pairs listed in diff/_versions_to_compare.md.
# Optionally set LLM_MODEL for this run.
#
# Usage:
#   ./setup.sh                    # uses LLM_MODEL from .env
#   ./setup.sh qwen3.5-27b        # sets LLM_MODEL and updates .env
set -euo pipefail
cd "$(dirname "$0")"

# Help
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: ./setup.sh [MODEL_NAME]"
    echo ""
    echo "Prepare all version pairs listed in diff/_versions_to_compare.md."
    echo "Optionally set LLM_MODEL for this run (updates .env)."
    echo ""
    echo "Examples:"
    echo "  ./setup.sh                 # uses LLM_MODEL from .env"
    echo "  ./setup.sh qwen3.5-27b    # sets LLM_MODEL and updates .env"
    exit 0
fi

# If model name provided, update .env
if [[ $# -ge 1 ]]; then
    MODEL="$1"
    if grep -q "^LLM_MODEL=" .env 2>/dev/null; then
        sed -i.bak "s/^LLM_MODEL=.*/LLM_MODEL=$MODEL/" .env
        rm -f .env.bak
    else
        echo "LLM_MODEL=$MODEL" >> .env
    fi
    export LLM_MODEL="$MODEL"
    echo "Set LLM_MODEL=$MODEL"
fi

# Verify LLM_MODEL is set
if [[ -z "${LLM_MODEL:-}" ]]; then
    # Try loading from .env
    if [[ -f .env ]]; then
        LLM_MODEL=$(grep "^LLM_MODEL=" .env | cut -d= -f2-)
        export LLM_MODEL
    fi
fi

# If still unset, create .env with default value
if [[ -z "${LLM_MODEL:-}" ]]; then
    LLM_MODEL="default"
    echo "LLM_MODEL=$LLM_MODEL" > .env
    export LLM_MODEL
    echo "Created .env with LLM_MODEL=default"
fi

echo "Model: $LLM_MODEL"
echo ""

VERSIONS_FILE="diff/_versions_to_compare.md"

if [[ ! -f "$VERSIONS_FILE" ]]; then
    echo "Error: $VERSIONS_FILE not found."
    echo "Create it with one OLD-NEW pair per line, e.g.:"
    echo "  9.00B4-9.00B5"
    exit 1
fi

prepared=0
failed=0

while IFS= read -r line || [[ -n "$line" ]]; do
    # Strip comments and whitespace
    pair="${line%%#*}"
    pair="${pair#"${pair%%[![:space:]]*}"}"
    pair="${pair%"${pair##*[![:space:]]}"}"
    [[ -z "$pair" ]] && continue

    V1="${pair%%-*}"
    V2="${pair#*-}"

    echo "=== $V1 -> $V2 ==="
    if python3 diff-tools/pipeline.py prepare "$V1" "$V2"; then
        ((prepared++))
    else
        echo "FAILED: $V1-$V2"
        ((failed++))
    fi
    echo ""
done < "$VERSIONS_FILE"

echo "Done. Prepared: $prepared, Failed: $failed"
