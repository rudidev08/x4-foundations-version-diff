#!/usr/bin/env bash
# Thin wrapper around `codex exec` that returns ONLY the final assistant
# message on stdout. `codex exec` normally interleaves session headers,
# "tokens used" summaries, and duplicate final-message prints on stdout —
# unusable as a 04_llm `LLM_CMD`. We funnel codex's noisy output to stderr
# and rely on --output-last-message to capture the clean reply.
#
# All args are forwarded to `codex exec`. Example:
#   ./src/codex-wrap.sh -m gpt-5.4 -s read-only --skip-git-repo-check --color never

set -euo pipefail

tmp="$(mktemp)"
trap "rm -f '$tmp'" EXIT

codex exec "$@" --output-last-message "$tmp" >&2
cat "$tmp"
