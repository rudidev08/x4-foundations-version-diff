#!/usr/bin/env bash
# X4 changelog pipeline runner.
#
#   ./run.sh --v1 DIR --v2 DIR [--model NAME] [--force-split] [--workers N]
#            [--strict-findings]
#
# Loads .env (resolves the active model profile), writes/validates
# artifacts/<run>/settings.json, then runs steps 01–05 in order.
# Each step is resumable — safe to re-run after any failure.

set -euo pipefail

usage() {
  cat <<EOF
Usage: $0 --v1 VERSION --v2 VERSION [--model NAME] [--force-split] [--llm-calls N]
                                 [--workers N] [--strict-findings]

    --v1 VERSION      "Before" source. Prefixed with SOURCE_PATH_PREFIX from
                      .env (default "x4-data/") unless absolute. e.g. 9.00B4.
    --v2 VERSION      "After" source, same rules. e.g. 9.00B5.
    --model NAME      LLM model (overrides DEFAULT_MODEL from .env).
    --force-split     Fall back to line-based cuts when structural splitting fails.
    --llm-calls N     Cap on fresh LLM calls this run (quota throttle). Cached
                      findings don't count. Stops cleanly at the limit; re-run
                      to continue.
    --workers N       Parallel LLM workers after first-call approval in step 04.
                      Defaults to 4.
    --strict-findings Abort step 05 if any finding block lacks a valid
                      [entity:key] prefix instead of normalizing it.
EOF
  exit 1
}

count_findings_progress() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

art = Path(sys.argv[1])
chunks = {p.stem for p in (art / "03_chunk" / "chunks").glob("*.txt")}
findings = {p.stem for p in (art / "04_llm" / "findings").glob("*.md")}
missing = chunks - findings
print(f"{len(missing)} {len(findings)} {len(chunks)}")
PY
}

resolve_llm_cmd() {
  python3 - "$1" "$2" <<'PY'
import os
import shlex
import sys
from pathlib import Path

root = Path(sys.argv[1])
cmd = sys.argv[2]
parts = shlex.split(cmd)
resolved: list[str] = []
for part in parts:
    if "/" in part and not os.path.isabs(part) and not part.startswith("-"):
        resolved.append(str((root / part).resolve()))
    else:
        resolved.append(part)
print(shlex.join(resolved))
PY
}

ROOT="$(cd "$(dirname "$0")" && pwd)"

if [[ -f "$ROOT/.env" ]]; then
  set -a; source "$ROOT/.env"; set +a
fi

V1=""; V2=""
MODEL="${DEFAULT_MODEL:-opus-max}"
FORCE_SPLIT_FLAG=""
LLM_CALLS_FLAG=""
WORKERS_FLAG=""
STRICT_FINDINGS_FLAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --v1)          V1="$2"; shift 2 ;;
    --v2)          V2="$2"; shift 2 ;;
    --model)       MODEL="$2"; shift 2 ;;
    --force-split) FORCE_SPLIT_FLAG="--force-split"; shift ;;
    --llm-calls)   LLM_CALLS_FLAG="--llm-calls $2"; shift 2 ;;
    --workers)     WORKERS_FLAG="--workers $2"; shift 2 ;;
    --strict-findings) STRICT_FINDINGS_FLAG="--strict-findings"; shift ;;
    -h|--help)     usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

[[ -z "$V1" || -z "$V2" ]] && usage

# Apply SOURCE_PATH_PREFIX (from .env) to V1/V2 unless they're already absolute.
PREFIX="${SOURCE_PATH_PREFIX:-}"
if [[ -n "$PREFIX" ]]; then
  [[ "$V1" != /* ]] && V1="${PREFIX}${V1}"
  [[ "$V2" != /* ]] && V2="${PREFIX}${V2}"
fi

# Sanity: fail early with a clear message if the resolved paths don't exist.
for d in "$V1" "$V2"; do
  if [[ ! -d "$d" ]]; then
    echo "source dir not found: $d" >&2
    echo "  check --v1 / --v2 and SOURCE_PATH_PREFIX in .env" >&2
    exit 1
  fi
done

# Find <KEY>_MODEL_NAME=$MODEL in the loaded env.
MODEL_KEY=""
for var in $(compgen -A variable | grep '_MODEL_NAME$' || true); do
  if [[ "${!var}" == "$MODEL" ]]; then
    MODEL_KEY="${var%_MODEL_NAME}"
    break
  fi
done
if [[ -z "$MODEL_KEY" ]]; then
  echo "No model matching '$MODEL' (looked for <KEY>_MODEL_NAME=$MODEL in .env)." >&2
  exit 1
fi

MODEL_NAME="$MODEL"
LLM_CMD_VAR="${MODEL_KEY}_LLM_CMD"
CHUNK_KB_VAR="${MODEL_KEY}_CHUNK_KB"
LLM_CMD="${!LLM_CMD_VAR}"
CHUNK_KB="${!CHUNK_KB_VAR}"
LLM_CMD="$(resolve_llm_cmd "$ROOT" "$LLM_CMD")"

V1_NAME="$(basename "$V1")"
V2_NAME="$(basename "$V2")"
ART="$ROOT/artifacts/${V1_NAME}_to_${V2_NAME}_${MODEL_NAME}"
OUT="$ROOT/output/${V1_NAME}_to_${V2_NAME}_${MODEL_NAME}.md"

mkdir -p "$ART" "$ROOT/output"

cat <<EOF
==[ X4 changelog pipeline ]==
  V1       : $V1_NAME  ($V1)
  V2       : $V2_NAME  ($V2)
  Model    : $MODEL_NAME (chunk_kb=$CHUNK_KB${FORCE_SPLIT_FLAG:+, force-split})
  Artifact : $ART
  Output   : $OUT
EOF

python3 "$ROOT/src/_ensure_settings.py" \
  --out "$ART" \
  --v1 "$V1_NAME" --v2 "$V2_NAME" \
  --model "$MODEL_NAME" --llm-cmd "$LLM_CMD" \
  --chunk-kb "$CHUNK_KB" \
  ${FORCE_SPLIT_FLAG}

X4_STEP_PREFIX="[step 1/5]" \
  python3 "$ROOT/src/01_enumerate.py" --v1 "$V1" --v2 "$V2" --out "$ART"
X4_STEP_PREFIX="[step 2/5]" \
  python3 "$ROOT/src/02_diff.py"      --v1 "$V1" --v2 "$V2" --out "$ART"
X4_STEP_PREFIX="[step 3/5]" \
  python3 "$ROOT/src/03_chunk.py"     --v1 "$V1" --v2 "$V2" --out "$ART" --chunk-kb "$CHUNK_KB" ${FORCE_SPLIT_FLAG}
X4_STEP_PREFIX="[step 4/5]" \
  python3 "$ROOT/src/04_llm.py"       --out "$ART" --llm-cmd "$LLM_CMD" ${WORKERS_FLAG} ${LLM_CALLS_FLAG}

if [[ -n "$LLM_CALLS_FLAG" ]]; then
  COUNTS="$(count_findings_progress "$ART")"
  read -r MISSING_FINDINGS FINDING_COUNT CHUNK_COUNT <<< "$COUNTS"
  if (( MISSING_FINDINGS > 0 )); then
    rm -f "$OUT"
    echo
    echo "[pipeline] Paused after step 4: $FINDING_COUNT/$CHUNK_COUNT chunk findings present."
    echo "[pipeline] Re-run the same command to continue. Final changelog not assembled."
    exit 0
  fi
fi

X4_STEP_PREFIX="[step 5/5]" \
  python3 "$ROOT/src/05_assemble.py"  --out "$ART" \
  --v1-name "$V1_NAME" --v2-name "$V2_NAME" \
  --model "$MODEL_NAME" --changelog "$OUT" ${STRICT_FINDINGS_FLAG}

echo
echo "Done: $OUT"
