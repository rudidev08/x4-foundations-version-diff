#!/usr/bin/env python3
"""Feed one rule's pipeline output to an LLM and save release notes.

Usage:
    python3 scripts/release_notes_llm.py <pair_dir> <rule> --model NAME
        [--max-tokens N] [--compact] [--dry-run]

Example:
    python3 scripts/release_notes_llm.py artifacts/8.00H4-9.00B6-opus-4.7-max missiles \\
        --model opus-4.7-max

Writes one file per chunk:
    <pair_dir>/llm_<rule>[_compact][_chunkNofM].md

The pair_dir path encodes the model (versions + model name), so
per-chunk files don't repeat the model tag.

The model is run via the LLM_CMD shell command associated with the
profile in `.env`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.lib.llm_budget import est_tokens, pack_into_batches  # noqa: E402
from src.lib.rule_output import is_diagnostic  # noqa: E402


def _load_env(path: str = '.env') -> None:
    """Minimal .env loader. Reads KEY=VALUE lines into os.environ without
    overriding existing environment. Supports single/double-quoted values
    and `#` line comments. Silently skips if the file doesn't exist.
    """
    env_file = Path(__file__).resolve().parent.parent / path
    if not env_file.is_file():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()


def resolve_game_data(cli_override: str | None) -> Path:
    """Game-data root resolution order:
    1. CLI --game-data if passed.
    2. SOURCE_PATH_PREFIX env var if set.
    3. <repo>/x4-data default.

    Relative paths resolve from the project root.
    """
    project_root = Path(__file__).resolve().parent.parent
    raw = cli_override or os.environ.get('SOURCE_PATH_PREFIX') or 'x4-data'
    p = Path(raw)
    return p if p.is_absolute() else (project_root / p).resolve()


def resolve_profile(model_name: str) -> dict:
    """Look up the LLM profile in env by matching `<PREFIX>_MODEL_NAME` to
    `model_name`. Returns the dict of MODEL_NAME / LLM_CMD / CHUNK_KB.

    Raises if no matching profile exists — callers must always supply a
    model_name (the CLI flag is required).
    """
    for key, value in os.environ.items():
        if key.endswith('_MODEL_NAME') and value == model_name:
            prefix = key[:-len('_MODEL_NAME')]
            return {
                'MODEL_NAME': value,
                'LLM_CMD': os.environ.get(f'{prefix}_LLM_CMD') or '',
                'CHUNK_KB': os.environ.get(f'{prefix}_CHUNK_KB') or '',
            }
    known = sorted(v for k, v in os.environ.items()
                   if k.endswith('_MODEL_NAME') and v)
    raise ValueError(
        f'--model={model_name!r} does not match any *_MODEL_NAME in env. '
        f'Known: {known}')


def resolve_max_tokens(cli_override: int | None,
                        profile: dict,
                        default: int = 24000) -> int:
    """Budget resolution order:
    1. CLI --max-tokens if passed.
    2. X4_LLM_MAX_TOKENS env var if set.
    3. Active profile's CHUNK_KB (KB -> tokens via /4 chars/token).
    4. Hard default.
    """
    if cli_override is not None:
        return cli_override
    explicit = os.environ.get('X4_LLM_MAX_TOKENS')
    if explicit:
        return int(explicit)
    if profile.get('CHUNK_KB'):
        # 1 KB of chars ≈ 256 tokens at the 4-chars/token rule of thumb.
        return int(profile['CHUNK_KB']) * 256
    return default


PROMPT = """\
You are writing release notes for X4 Foundations, a complex space sandbox game.

I will give you the structured output from one rule that diffs two game
versions. Each record in the JSON is one thing that changed. Your job is to
turn the raw records into short, human-readable release notes that a player
would actually want to read.

Guidelines:
- Group by theme. For missiles: new missiles, deprecations, balance changes,
  DLC-specific additions. For other rule kinds, pick themes that fit the data.
- One to three short sentences per group. No bullet-list soup of every
  individual entity.
- Plain English. No internal XML field names, no "RuleOutput", no "DiffReport".
  If a missile has `extras.classifications = ["small", "guided", "mk1"]`, write
  "small guided mk1" not `["small", "guided", "mk1"]`.
- Surface patterns and callouts. If 15 missiles had their damage bumped and
  2 had speed changed, say that.
- When a record carries a before/after numeric value (e.g. `stat_diff`,
  `changes`, a "X→Y" in the `text`), QUOTE THE NUMBERS. Prefer "hull
  24000→48000" over "much tougher"; prefer "price cut 120502→12502"
  over "significant price drop". Vague words like "significantly",
  "slightly", "much", "broadly" should not replace numbers you already
  have.
- If a change adds a cue, listener, trigger, or system with a feature
  gate (e.g. `check_value value="false"`, a TODO/"keep disabled"
  comment, or similar), describe it as ADDED BUT DISABLED — do NOT
  present it as live gameplay. Quote the gating evidence briefly if the
  diff makes it clear.
- Script properties (gamelogic records with `kind=NEW` or `REMOVED` on
  `scriptproperty` entries) are modding API changes, not internal
  jargon. Call them out in a short "Under the Hood" (or equivalent)
  section so mod authors see what's new or gone.
- End with a one-sentence summary.
- Do NOT invent numbers or facts. If the JSON doesn't say something, don't
  claim it.

Context: this is the `{rule}` rule diffing `{old_version}` to `{new_version}`.
There are {count} records below.

```json
{data}
```

Write the release notes now.
"""


def _compact_record(record: dict, diff_head_lines: int = 8) -> dict:
    """Strip heavy fields from a record so it fits in a shared LLM prompt.

    The `extras.diff` field can be tens of kilobytes per file; instead we
    keep a short head excerpt that conveys what changed without the full
    body. Line counts and paths survive intact.
    """
    extras = record.get('extras', {})
    if isinstance(extras, dict) and 'diff' in extras and extras['diff']:
        lines = extras['diff'].splitlines()
        head = '\n'.join(lines[:diff_head_lines])
        more = len(lines) - diff_head_lines
        if more > 0:
            head += f'\n... ({more} more diff lines trimmed)'
        extras = {**extras, 'diff': head}
    return {**record, 'extras': extras}


def _chunk_records(records: list[dict], max_tokens_per_chunk: int
                   ) -> list[list[dict]]:
    """Split records into chunks, each under max_tokens_per_chunk.

    Groups by primary classification so related files stay together within
    a chunk. Single records larger than the budget get their own chunk
    (and will still fail if they exceed the model's hard limit — caller
    should either reduce max_tokens_per_chunk or use compact mode).
    """
    # Stable sort: keep insertion order within a classification group.
    def sort_key(record):
        extras = record.get('extras') or {}
        classifications = extras.get('classifications') or ['']
        return (str(classifications[0]) if classifications else '', str(extras.get('entity_key') or ''))
    sorted_recs = sorted(records, key=sort_key)
    return pack_into_batches(sorted_recs, max_tokens_per_chunk, overhead=0)


def build_prompts(pair_dir: Path, rule: str, compact: bool = False,
                  max_tokens: int | None = None,
                  drop_diagnostic: bool = True) -> list[str]:
    """Build one or more prompts covering the rule's records.

    If max_tokens is None, returns a single prompt with every record. If
    max_tokens is set, records are split into chunks each under that many
    tokens (estimated). Diagnostic rows (incomplete sentinels, warnings)
    are dropped by default since they're for debugging, not release notes.
    """
    summary = json.loads((pair_dir / 'summary.json').read_text())
    records = json.loads((pair_dir / f'{rule}.json').read_text())
    if drop_diagnostic:
        records = [r for r in records if not is_diagnostic(r)]
    if compact:
        records = [_compact_record(r) for r in records]

    prompt_overhead = est_tokens(PROMPT.format(
        rule=rule, old_version=summary['old_version'],
        new_version=summary['new_version'], count=0, data=''))

    if max_tokens is None:
        return [PROMPT.format(
            rule=rule,
            old_version=summary['old_version'],
            new_version=summary['new_version'],
            count=len(records),
            data=json.dumps(records, ensure_ascii=False),
        )]

    chunk_budget = max_tokens - prompt_overhead - 500  # slack
    if chunk_budget < 1000:
        raise ValueError(f'max_tokens={max_tokens} too small after overhead')
    chunks = _chunk_records(records, chunk_budget)
    return [PROMPT.format(
        rule=rule,
        old_version=summary['old_version'],
        new_version=summary['new_version'],
        count=len(chunk),
        data=json.dumps(chunk, ensure_ascii=False),
    ) for chunk in chunks]


class LLMError(RuntimeError):
    """Raised when the configured LLM invocation returns a non-zero exit.
    Nothing is written to disk when this fires — the caller is expected
    to propagate it so the user sees the failure and can re-run to
    resume from the last successful chunk.
    """


def _invoke(argv: list[str], prompt: str, label: str) -> str:
    result = subprocess.run(argv, input=prompt, capture_output=True,
                            text=True, check=False)
    if result.returncode != 0:
        raise LLMError(
            f'LLM call failed ({label}) — exit {result.returncode}\n'
            f'--- stderr ---\n{result.stderr}\n'
            f'--- stdout ---\n{result.stdout[:2000]}'
        )
    if not result.stdout.strip():
        raise LLMError(
            f'LLM call returned empty output ({label})\n'
            f'--- stderr ---\n{result.stderr}'
        )
    return result.stdout


def run_llm_cmd(prompt: str, cmd: str) -> str:
    """Run a shell command (from a .env profile) piping prompt to stdin."""
    if not cmd:
        raise ValueError('empty LLM_CMD — check .env profile')
    return _invoke(['/bin/sh', '-c', cmd], prompt, label=f'profile cmd={cmd!r}')


def run_llm(prompt: str, *, profile: dict) -> str:
    """Run the LLM_CMD from the active .env profile."""
    return run_llm_cmd(prompt, profile['LLM_CMD'])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument('pair_dir')
    parser.add_argument('rule')
    parser.add_argument('--model', required=True,
                        help='Active LLM profile (matches a *_MODEL_NAME '
                             'entry in .env).')
    advanced = parser.add_argument_group(
        'advanced (rarely needed — defaults are usually correct)')
    advanced.add_argument('--compact', action='store_true',
                          help='Trim extras.diff to a short head excerpt '
                               '(for rules with large embedded diffs like '
                               'quests/gamelogic).')
    advanced.add_argument('--max-tokens', type=int, default=None,
                          help='Split the prompt into chunks each under this '
                               'many tokens. Overrides .env CHUNK_KB and '
                               'X4_LLM_MAX_TOKENS.')
    advanced.add_argument('--dry-run', action='store_true',
                          help='Print per-chunk sizes and counts, but do '
                               'not call the LLM.')
    args = parser.parse_args()

    pair_dir = Path(args.pair_dir)
    if not (pair_dir / f'{args.rule}.json').exists():
        sys.exit(f'missing rule output: {pair_dir}/{args.rule}.json')

    profile = resolve_profile(args.model)
    max_tokens = resolve_max_tokens(args.max_tokens, profile)

    prompts = build_prompts(pair_dir, args.rule, compact=args.compact,
                            max_tokens=max_tokens)
    suffix = '_compact' if args.compact else ''
    tag = profile['MODEL_NAME']

    for i, prompt in enumerate(prompts):
        chunk_tag = f'_chunk{i+1}of{len(prompts)}' if len(prompts) > 1 else ''
        out_path = pair_dir / f'llm_{args.rule}{suffix}{chunk_tag}.md'
        print(f'Chunk {i+1}/{len(prompts)}: {len(prompt)} chars '
              f'(~{len(prompt) // 4} tokens)')
        if args.dry_run:
            continue
        # Resumable: skip chunks we already produced so a crashed run
        # restarts from the last unfinished chunk.
        if out_path.exists() and out_path.stat().st_size > 0:
            print(f'  skip: {out_path} exists ({out_path.stat().st_size} bytes)')
            continue
        print(f'Running LLM (tag={tag}, budget={max_tokens} tokens)...')
        response = run_llm(prompt, profile=profile)
        out_path.write_text(response)
        print(f'Wrote {out_path} ({len(response)} chars)')


if __name__ == '__main__':
    main()
