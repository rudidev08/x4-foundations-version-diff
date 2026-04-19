#!/usr/bin/env bash
# One-shot release-notes generator.
#
# Usage:
#   ./run.sh <old_version> <new_version> --model <PROFILE>
#
# Example:
#   ./run.sh 8.00H4 9.00B6 --model gpt-5.4-mini-low
#
# `--model` must match a *_MODEL_NAME entry in .env. Run
# `./run.sh --help` for the full option list. Intermediate files go to
# artifacts/<old>_<new>/; final release notes land in output/.
set -e
cd "$(dirname "$0")"
exec python3 scripts/generate_release_notes.py "$@"
