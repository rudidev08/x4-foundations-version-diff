#!/usr/bin/env bash
# One-shot release-notes generator.
#
# Usage:
#   ./run.sh <old_version> <new_version> [extra flags]
#
# Examples:
#   ./run.sh 8.00H4 9.00B6
#   ./run.sh 9.00B5 9.00B6 --model haiku
#   ./run.sh 8.00H4 9.00B6 --reasoning medium
#
# Intermediate files go to artifacts/<old>_<new>/; the final release
# notes land in output/<old>_<new>/. See README.md and .env.example.
set -e
cd "$(dirname "$0")"
exec python3 scripts/generate_release_notes.py "$@"
