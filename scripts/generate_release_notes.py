#!/usr/bin/env python3
"""End-to-end release-notes generator.

Runs three stages for a version pair:

  1. scripts/run_rules.py         — produce rule JSON under artifacts/<pair>/
  2. scripts/release_notes_llm.py — one LLM pass per rule, one file per
                                     chunk under artifacts/<pair>/
  3. scripts/aggregate_release_notes.py — tree-reduce merge per rule,
                                     then top-level RELEASE_NOTES_<tag>.md
                                     under output/<pair>/

Every stage is idempotent: outputs that already exist on disk are
skipped. A failed run can be resumed just by rerunning the same command.

When any LLM call fails, the pipeline stops immediately and prints the
error. The failing chunk is not written to disk, so the next run picks
up from that chunk.

Usage:
    ./run.sh 8.00H4 9.00B6
    python3 scripts/generate_release_notes.py 8.00H4 9.00B6 [--model NAME]

If no .env profile is active and no --reasoning flag is passed,
xhigh is used by default (highest-quality single-call reasoning for the
fallback Codex path).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import change_map  # noqa: E402
from scripts.release_notes_llm import (  # noqa: E402
    LLMError, _resolve_profile, _resolve_max_tokens,
)
from scripts.run_rules import RULES as ALL_RULES  # noqa: E402


def _run(argv: list[str]) -> None:
    """Run a subprocess, stream its output to ours, exit on failure."""
    print(f'\n$ {" ".join(argv)}')
    result = subprocess.run(argv, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f'\nCommand failed: {" ".join(argv)} (exit {result.returncode})')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('old_version', help='e.g. 8.00H4')
    ap.add_argument('new_version', help='e.g. 9.00B6')
    ap.add_argument('--game-data', default=str(ROOT / 'x4-data'),
                    help='Directory containing the extracted X4 version '
                         'folders (default: ./x4-data).')
    ap.add_argument('--artifacts', default=str(ROOT / 'artifacts'),
                    help='Output root (default: ./artifacts)')
    ap.add_argument('--model',
                    help='Override DEFAULT_MODEL from .env.')
    ap.add_argument('--reasoning',
                    choices=['low', 'medium', 'high', 'xhigh'],
                    help='Legacy Codex reasoning level, used only when no '
                         '.env profile is active. Defaults to xhigh.')
    ap.add_argument('--max-tokens', type=int, default=None,
                    help='Override the per-call token budget (otherwise '
                         'comes from active profile CHUNK_KB or default).')
    args = ap.parse_args()

    game_data = Path(args.game_data)
    for v in (args.old_version, args.new_version):
        if not (game_data / v).is_dir():
            sys.exit(f'missing game-data version folder: {game_data / v}')

    profile = _resolve_profile(args.model)
    tag = profile['MODEL_NAME'] if profile else (args.reasoning or 'xhigh')
    if not profile and not args.reasoning:
        args.reasoning = 'xhigh'

    pair_dir = Path(args.artifacts) / f'{args.old_version}_{args.new_version}'
    pair_dir.mkdir(parents=True, exist_ok=True)

    print(f'=== release notes: {args.old_version} -> {args.new_version} '
          f'(tag={tag}) ===')
    print(f'artifacts: {pair_dir}')
    if profile:
        budget = _resolve_max_tokens(args.max_tokens, profile)
        print(f'active profile: {profile["MODEL_NAME"]}  budget={budget} tokens')

    # --- Stage 1: rule JSON ---
    # Re-run cheap; run_rules.py is idempotent in effect because rule
    # outputs overwrite cleanly and each rule completes fast.
    run_rules_argv = [
        'python3', 'scripts/run_rules.py',
        args.old_version, args.new_version,
        '--game-data', args.game_data,
        '--out', args.artifacts,
    ]
    summary = pair_dir / 'summary.json'
    if summary.exists():
        print(f'\n[1/3] rule JSON: {summary.name} exists, skipping')
    else:
        _run(run_rules_argv)

    # --- Stage 2: LLM per-rule chunks ---
    print(f'\n[2/3] LLM per-rule pass (skips existing chunk files)')
    for rule in ALL_RULES:
        rule_json = pair_dir / f'{rule}.json'
        if not rule_json.exists():
            continue
        # Build the release_notes_llm invocation. Use --model when a
        # profile is active; fall back to positional reasoning otherwise.
        argv = ['python3', 'scripts/release_notes_llm.py',
                str(pair_dir), rule]
        if profile:
            argv += ['--model', profile['MODEL_NAME']]
        else:
            argv += [args.reasoning]
        if args.max_tokens is not None:
            argv += ['--max-tokens', str(args.max_tokens)]
        _run(argv)

    # --- Stage 3: aggregate ---
    print(f'\n[3/3] aggregate (skips cached rule aggregates + top-level)')
    agg_argv = ['python3', 'scripts/aggregate_release_notes.py',
                str(pair_dir), tag]
    if profile:
        agg_argv += ['--model', profile['MODEL_NAME']]
    if args.max_tokens is not None:
        agg_argv += ['--max-tokens', str(args.max_tokens)]
    _run(agg_argv)

    final = ROOT / 'output' / pair_dir.name / f'RELEASE_NOTES_{tag}.md'
    print(f'\nDone. Release notes: {final}')


if __name__ == '__main__':
    main()
