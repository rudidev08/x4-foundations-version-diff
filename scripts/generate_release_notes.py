#!/usr/bin/env python3
"""End-to-end release-notes generator.

Runs four stages for a version pair. Intermediates live in a per-model
artifact folder at `artifacts/<old>-<new>-<MODEL>/` so parallel runs on
different models never collide. Deliverables (raw + final markdown)
land under `output/`.

  1. scripts/run_rules.py            — rule JSON under the pair dir
  2. scripts/raw_release_notes.py    — deterministic raw notes
                                       (output/<old>-<new>-<MODEL>-raw.md)
  3. scripts/release_notes_llm.py    — one LLM pass per rule, one file per
                                       chunk under the pair dir
  4. scripts/aggregate_release_notes.py — tree-reduce merge per rule, then
                                       top-level final notes at
                                       output/<old>-<new>-<MODEL>.md

Every stage is idempotent: outputs that already exist on disk are
skipped. A failed run can be resumed just by rerunning the same command.

Usage:
    ./run.sh 8.00h4 9.00b6 --model gpt-5.5-mini-low
    python3 scripts/generate_release_notes.py 8.00h4 9.00b6 --model NAME

`--model` is required and must match a `*_MODEL_NAME` entry in `.env`.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.release_notes_llm import (  # noqa: E402
    available_models, resolve_profile, resolve_max_tokens, resolve_game_data,
)
from scripts.run_rules import RULES as ALL_RULES  # noqa: E402


def _run(argv: list[str]) -> None:
    """Run a subprocess, stream its output to ours, exit on failure."""
    print(f'\n$ {" ".join(argv)}')
    result = subprocess.run(argv, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f'\nCommand failed: {" ".join(argv)} (exit {result.returncode})')


def _available_versions(game_data: Path) -> list[str]:
    if not game_data.is_dir():
        return []
    return sorted(p.name for p in game_data.iterdir() if p.is_dir())


def _versions_line(game_data: Path) -> str:
    versions = _available_versions(game_data)
    body = ', '.join(versions) if versions else f'(none found in {game_data})'
    return f'available versions: {body}'


def _models_line() -> str:
    models = available_models()
    body = ', '.join(models) if models else '(none — no *_MODEL_NAME entries in .env)'
    return f'available models:   {body}'


def _format_hints(game_data: Path) -> str:
    """Both hint lines, joined. Reused for argparse errors and the `--help`
    epilog so the two sources can never drift.
    """
    return f'{_versions_line(game_data)}\n{_models_line()}'


class _HintingParser(argparse.ArgumentParser):
    """argparse parser that appends the list of valid versions and models
    to every error message, so the user never has to guess what's allowed.
    """

    def __init__(self, *args, hint_game_data: Path, **kwargs):
        super().__init__(*args, **kwargs)
        self._hint_game_data = hint_game_data

    def error(self, message: str) -> NoReturn:
        super().error(message + '\n' + _format_hints(self._hint_game_data))


def _preflight(game_data: Path, old_version: str, new_version: str,
               model: str) -> dict:
    """Validate versions and model before any subprocess runs.

    Exits listing only the option set relevant to what's wrong. On
    success, returns the resolved profile so the caller skips a second
    lookup.
    """
    bad_versions = [v for v in (old_version, new_version)
                    if not (game_data / v).is_dir()]
    try:
        profile = resolve_profile(model)
    except ValueError:
        profile = None

    if not bad_versions and profile is not None:
        return profile

    lines = [f'unknown version {v!r}' for v in bad_versions]
    if profile is None:
        lines.append(f'unknown model {model!r}')
    if bad_versions:
        lines.append(_versions_line(game_data))
    if profile is None:
        lines.append(_models_line())
    sys.exit('\n'.join(lines))


def main():
    default_game_data = resolve_game_data(None)
    parser = _HintingParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        epilog=_format_hints(default_game_data),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        hint_game_data=default_game_data,
    )
    parser.add_argument('old_version', help='e.g. 8.00h4 (see "available versions" below)')
    parser.add_argument('new_version', help='e.g. 9.00b6 (see "available versions" below)')
    parser.add_argument('--model', required=True,
                        help='Active LLM profile. Must match a *_MODEL_NAME '
                             'entry in .env (see "available models" below).')
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
    profile = _preflight(game_data, args.old_version, args.new_version, args.model)
    tag = profile['MODEL_NAME']
    budget = resolve_max_tokens(args.max_tokens, profile)

    pair_dir = (Path(args.artifacts) /
                f'{args.old_version}-{args.new_version}-{tag}')
    pair_dir.mkdir(parents=True, exist_ok=True)

    print(f'=== release notes: {args.old_version} -> {args.new_version} '
          f'(model={tag}, budget={budget} tokens) ===')
    print(f'artifacts: {pair_dir}')

    # --- Stage 1: rule JSON ---
    run_rules_argv = [
        'python3', 'scripts/run_rules.py',
        args.old_version, args.new_version,
        '--game-data', str(game_data),
        '--out', str(pair_dir),
    ]
    summary = pair_dir / 'summary.json'
    if summary.exists():
        print(f'\n[1/4] rule JSON: {summary.name} exists, skipping')
    else:
        _run(run_rules_argv)

    # --- Stage 2: deterministic raw notes (no LLM) ---
    print('\n[2/4] raw release notes (deterministic, always regenerated)')
    _run(['python3', 'scripts/raw_release_notes.py',
          str(pair_dir), '--model', tag])

    # --- Stage 3: LLM per-rule chunks ---
    print('\n[3/4] LLM per-rule pass (skips existing chunk files)')
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
    print('\n[4/4] aggregate (skips cached rule aggregates + top-level)')
    agg_argv = ['python3', 'scripts/aggregate_release_notes.py',
                str(pair_dir), '--model', tag]
    if args.max_tokens is not None:
        agg_argv += ['--max-tokens', str(args.max_tokens)]
    _run(agg_argv)

    final = ROOT / 'output' / f'{args.old_version}-{args.new_version}-{tag}.md'
    print(f'\nDone. Release notes: {final}')


if __name__ == '__main__':
    main()
