#!/usr/bin/env python3
"""End-to-end release-notes generator.

Runs four stages for a version pair:

  1. scripts/run_rules.py            — produce rule JSON under artifacts/<pair>/
  2. scripts/raw_release_notes.py    — deterministic raw notes (no LLM)
  3. scripts/release_notes_llm.py    — one LLM pass per rule, one file per
                                       chunk under artifacts/<pair>/
  4. scripts/aggregate_release_notes.py — tree-reduce merge per rule, then
                                       top-level <old>-<new>-<MODEL>.md
                                       under output/

Every stage is idempotent: outputs that already exist on disk are
skipped. A failed run can be resumed just by rerunning the same command.

Usage:
    ./run.sh 8.00H4 9.00B6 --model gpt-5.4-mini-low
    python3 scripts/generate_release_notes.py 8.00H4 9.00B6 --model NAME

`--model` is required and must match a `*_MODEL_NAME` entry in `.env`.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.release_notes_llm import (  # noqa: E402
    resolve_profile, resolve_max_tokens, resolve_game_data,
)
from scripts.run_rules import RULES as ALL_RULES  # noqa: E402


def _run(argv: list[str]) -> None:
    """Run a subprocess, stream its output to ours, exit on failure."""
    print(f'\n$ {" ".join(argv)}')
    result = subprocess.run(argv, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f'\nCommand failed: {" ".join(argv)} (exit {result.returncode})')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('old_version', help='e.g. 8.00H4')
    parser.add_argument('new_version', help='e.g. 9.00B6')
    parser.add_argument('--model', required=True,
                        help='Active LLM profile (matches a *_MODEL_NAME '
                             'entry in .env). E.g. gpt-5.4-mini-low, '
                             'opus-4.7-max, haiku.')
    advanced = parser.add_argument_group(
        'advanced (rarely needed — defaults are usually correct)')
    advanced.add_argument('--game-data', default=None,
                          help='Directory containing the extracted X4 '
                               'version folders. Defaults to '
                               'SOURCE_PATH_PREFIX from .env, else '
                               './x4-data.')
    advanced.add_argument('--artifacts', default=str(ROOT / 'artifacts'),
                          help='Output root (default: ./artifacts)')
    advanced.add_argument('--max-tokens', type=int, default=None,
                          help='Override the per-call token budget '
                               '(otherwise comes from active profile '
                               'CHUNK_KB or default).')
    args = parser.parse_args()

    game_data = resolve_game_data(args.game_data)
    for v in (args.old_version, args.new_version):
        if not (game_data / v).is_dir():
            sys.exit(f'missing game-data version folder: {game_data / v}')

    profile = resolve_profile(args.model)
    tag = profile['MODEL_NAME']
    budget = resolve_max_tokens(args.max_tokens, profile)

    pair_dir = Path(args.artifacts) / f'{args.old_version}_{args.new_version}'
    pair_dir.mkdir(parents=True, exist_ok=True)

    print(f'=== release notes: {args.old_version} -> {args.new_version} '
          f'(model={tag}, budget={budget} tokens) ===')
    print(f'artifacts: {pair_dir}')

    # --- Stage 1: rule JSON ---
    run_rules_argv = [
        'python3', 'scripts/run_rules.py',
        args.old_version, args.new_version,
        '--game-data', str(game_data),
        '--out', args.artifacts,
    ]
    summary = pair_dir / 'summary.json'
    if summary.exists():
        print(f'\n[1/4] rule JSON: {summary.name} exists, skipping')
    else:
        _run(run_rules_argv)

    # --- Stage 2: deterministic raw notes (no LLM) ---
    print(f'\n[2/4] raw release notes (deterministic, always regenerated)')
    _run(['python3', 'scripts/raw_release_notes.py', str(pair_dir)])

    # --- Stage 3: LLM per-rule chunks ---
    print(f'\n[3/4] LLM per-rule pass (skips existing chunk files)')
    for rule in ALL_RULES:
        rule_json = pair_dir / f'{rule}.json'
        if not rule_json.exists():
            continue
        argv = ['python3', 'scripts/release_notes_llm.py',
                str(pair_dir), rule, '--model', tag]
        if args.max_tokens is not None:
            argv += ['--max-tokens', str(args.max_tokens)]
        _run(argv)

    # --- Stage 4: aggregate ---
    print(f'\n[4/4] aggregate (skips cached rule aggregates + top-level)')
    agg_argv = ['python3', 'scripts/aggregate_release_notes.py',
                str(pair_dir), '--model', tag]
    if args.max_tokens is not None:
        agg_argv += ['--max-tokens', str(args.max_tokens)]
    _run(agg_argv)

    final = ROOT / 'output' / f'{args.old_version}-{args.new_version}-{tag}.md'
    print(f'\nDone. Release notes: {final}')


if __name__ == '__main__':
    main()
