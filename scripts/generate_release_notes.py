#!/usr/bin/env python3
"""End-to-end release-notes generator.

Runs three stages for a version pair. Intermediates live in a per-model
artifact folder at `artifacts/<old>-<new>-<MODEL>/` so parallel runs on
different models never collide. The final markdown lands under `output/`.

  1. scripts/run_rules.py            — rule JSON under the pair dir
  2. scripts/release_notes_llm.py    — one LLM pass per rule, one file per
                                       chunk under the pair dir
  3. scripts/aggregate_release_notes.py — tree-reduce merge per rule, then
                                       top-level final notes at
                                       output/<old>-<new>-<MODEL>.md

Every stage is idempotent: outputs that already exist on disk are
skipped. A failed run can be resumed just by rerunning the same command.
The per-pair artifacts folder is deleted automatically once the final
output file is written successfully.

Usage:
    ./run.sh 8.00h4 9.00b6 --model gpt-5.5-mini-low
    python3 scripts/generate_release_notes.py 8.00h4 9.00b6 --model NAME

`--model` is required and must match a `*_MODEL_NAME` entry in `.env`.
"""
from __future__ import annotations

import argparse
import shutil
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
from src.lib.rule_output import final_notes_path  # noqa: E402


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
    return f'available models:     {body}'


def _categories_line() -> str:
    return f'available categories: {", ".join(ALL_RULES)}'


def _format_hints(game_data: Path) -> str:
    """All three hint lines, joined. Reused for argparse errors and the
    `--help` epilog so the two sources can never drift.
    """
    return (f'{_versions_line(game_data)}\n'
            f'{_models_line()}\n'
            f'{_categories_line()}')


def _parse_categories(raw: str | None) -> list[str] | None:
    """Split `--categories` input into a clean list, or return None if the
    flag was omitted (meaning: include every category).

    Empty entries (from a trailing comma or `,,`) are dropped. Whitespace
    around names is stripped, so `quests, gamelogic` still works even
    though the tooltip asks for no spaces.
    """
    if raw is None:
        return None
    return [c.strip() for c in raw.split(',') if c.strip()]


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
               model: str, categories: list[str] | None) -> dict:
    """Validate versions, model, and categories before any subprocess runs.

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
    bad_categories = ([c for c in categories if c not in ALL_RULES]
                      if categories is not None else [])
    # `--categories ""` / `--categories ,` parses to []; reject explicitly so
    # the user gets a clear error instead of an empty run that wastes stage 1
    # and then fails in aggregate with "no per-rule markdowns found".
    empty_categories = categories is not None and not categories

    if (not bad_versions and profile is not None
            and not bad_categories and not empty_categories):
        return profile

    lines = [f'unknown version {v!r}' for v in bad_versions]
    if profile is None:
        lines.append(f'unknown model {model!r}')
    for c in bad_categories:
        lines.append(f'unknown category {c!r}')
    if empty_categories:
        lines.append('--categories was empty; omit the flag to include all '
                     'categories, or list at least one name')
    if bad_versions:
        lines.append(_versions_line(game_data))
    if profile is None:
        lines.append(_models_line())
    if bad_categories or empty_categories:
        lines.append(_categories_line())
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
    parser.add_argument('-m', '--model', required=True,
                        help='Active LLM profile. Must match a *_MODEL_NAME '
                             'entry in .env (see "available models" below).')
    parser.add_argument('-c', '--categories', default=None,
                        help='Comma-separated category names to include in '
                             'the report (no spaces). Omit to include every '
                             'category. Example: '
                             '--categories quests,gamelogic,ships. See '
                             '"available categories" below for the full list.')
    advanced = parser.add_argument_group(
        'advanced (rarely needed — defaults are usually correct)')
    advanced.add_argument('-g', '--game-data', default=None,
                          help='Directory containing the extracted X4 '
                               'version folders. Defaults to '
                               'SOURCE_PATH_PREFIX from .env, else '
                               './x4-data.')
    advanced.add_argument('-a', '--artifacts', default=str(ROOT / 'artifacts'),
                          help='Output root (default: ./artifacts)')
    advanced.add_argument('-b', '--max-tokens', type=int, default=None,
                          help='Override the per-call token budget '
                               '(otherwise comes from active profile '
                               'CHUNK_KB or default).')
    args = parser.parse_args()

    game_data = resolve_game_data(args.game_data)
    categories = _parse_categories(args.categories)
    profile = _preflight(game_data, args.old_version, args.new_version,
                         args.model, categories)
    tag = profile['MODEL_NAME']
    budget = resolve_max_tokens(args.max_tokens, profile)
    selected_rules = categories if categories is not None else ALL_RULES

    pair_dir = (Path(args.artifacts) /
                f'{args.old_version}-{args.new_version}-{tag}')
    pair_dir.mkdir(parents=True, exist_ok=True)

    print(f'=== release notes: {args.old_version} -> {args.new_version} '
          f'(model={tag}, budget={budget} tokens) ===')
    print(f'artifacts: {pair_dir}')

    run_rules_argv = [
        'python3', 'scripts/run_rules.py',
        args.old_version, args.new_version,
        '--game-data', str(game_data),
        '--out', str(pair_dir),
    ]
    if categories is not None:
        run_rules_argv += ['--only', ','.join(categories)]
    summary = pair_dir / 'summary.json'
    if summary.exists():
        print(f'\n[1/3] rule JSON: {summary.name} exists, skipping')
    else:
        _run(run_rules_argv)

    print('\n[2/3] LLM per-rule pass (skips existing chunk files)')
    for rule in selected_rules:
        rule_json = pair_dir / f'{rule}.json'
        if not rule_json.exists():
            continue
        argv = ['python3', 'scripts/release_notes_llm.py',
                str(pair_dir), rule, '--model', tag]
        if args.max_tokens is not None:
            argv += ['--max-tokens', str(args.max_tokens)]
        _run(argv)

    print('\n[3/3] aggregate (skips cached rule aggregates + top-level)')
    agg_argv = ['python3', 'scripts/aggregate_release_notes.py',
                str(pair_dir), '--model', tag]
    if args.max_tokens is not None:
        agg_argv += ['--max-tokens', str(args.max_tokens)]
    _run(agg_argv)

    final = final_notes_path(ROOT, args.old_version, args.new_version, tag)
    if final.exists() and final.stat().st_size > 0:
        shutil.rmtree(pair_dir)
        artifacts_root = pair_dir.parent
        cleanup_note = f'cleaned up artifacts: {pair_dir}'
        try:
            artifacts_root.rmdir()
            cleanup_note += f' (and removed empty {artifacts_root}/)'
        except OSError:
            pass
        print(f'\nDone. Release notes: {final}\n{cleanup_note}')
    else:
        sys.exit(f'\nfinal output missing or empty: {final}\n'
                 f'artifacts kept for resume: {pair_dir}')


if __name__ == '__main__':
    main()
