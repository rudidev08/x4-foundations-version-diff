#!/usr/bin/env python3
"""Feed one rule's pipeline output to Codex and save release notes.

Usage:
    python3 scripts/release_notes_llm.py <pair_dir> <rule> <reasoning>

Example:
    python3 scripts/release_notes_llm.py output/8.00H4_9.00B6 missiles xhigh

Reasoning levels: low, medium, xhigh.

Writes:
    <pair_dir>/llm_<rule>_<reasoning>.md

Runs Codex CLI via the harness `codex` command. Script is a thin wrapper —
the prompt template lives in PROMPT below so it's easy to iterate on.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


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


def _compact_record(rec: dict, diff_head_lines: int = 8) -> dict:
    """Strip heavy fields from a record so it fits in a shared LLM prompt.

    The `extras.diff` field can be tens of kilobytes per file; instead we
    keep a short head excerpt that conveys what changed without the full
    body. Line counts and paths survive intact.
    """
    extras = rec.get('extras', {})
    if isinstance(extras, dict) and 'diff' in extras and extras['diff']:
        lines = extras['diff'].splitlines()
        head = '\n'.join(lines[:diff_head_lines])
        more = len(lines) - diff_head_lines
        if more > 0:
            head += f'\n... ({more} more diff lines trimmed)'
        extras = {**extras, 'diff': head}
    return {**rec, 'extras': extras}


def _est_tokens(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False)) // 4


def _chunk_records(records: list[dict], max_tokens_per_chunk: int
                   ) -> list[list[dict]]:
    """Split records into chunks, each under max_tokens_per_chunk.

    Groups by primary classification so related files stay together within
    a chunk. Single records larger than the budget get their own chunk
    (and will still fail if they exceed the model's hard limit — caller
    should either reduce max_tokens_per_chunk or use compact mode).
    """
    # Stable sort: keep insertion order within a classification group.
    def sort_key(rec):
        extras = rec.get('extras') or {}
        cls = extras.get('classifications') or ['']
        return (str(cls[0]) if cls else '', str(extras.get('entity_key') or ''))
    sorted_recs = sorted(records, key=sort_key)

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_size = 0
    for rec in sorted_recs:
        rec_size = _est_tokens(rec)
        if current and current_size + rec_size > max_tokens_per_chunk:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(rec)
        current_size += rec_size
    if current:
        chunks.append(current)
    return chunks


def _is_diagnostic(rec: dict) -> bool:
    """Diagnostic rows (incomplete sentinels, warnings) carry large failure/
    warning payloads useful for debugging but not for release notes. Filter
    them out before chunking so the LLM sees only player-facing content.
    """
    extras = rec.get('extras') or {}
    return extras.get('kind') in ('incomplete', 'warning')


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
        records = [r for r in records if not _is_diagnostic(r)]
    if compact:
        records = [_compact_record(r) for r in records]

    prompt_overhead = _est_tokens(PROMPT.format(
        rule=rule, old_version=summary['old_version'],
        new_version=summary['new_version'], count=0, data=''))

    if max_tokens is None:
        return [PROMPT.format(
            rule=rule,
            old_version=summary['old_version'],
            new_version=summary['new_version'],
            count=len(records),
            data=json.dumps(records, indent=2, ensure_ascii=False),
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
        data=json.dumps(chunk, indent=2, ensure_ascii=False),
    ) for chunk in chunks]


def build_prompt(pair_dir: Path, rule: str, compact: bool = False) -> str:
    """Back-compat: single prompt, no chunking."""
    return build_prompts(pair_dir, rule, compact=compact)[0]


def run_codex(prompt: str, reasoning: str) -> str:
    """Invoke the Codex CLI with the given reasoning level. Returns stdout.

    Reasoning is passed via `-c model_reasoning_effort=<level>` config
    override. `codex exec` reads the prompt from stdin when `-` is passed
    (or when nothing is passed as the prompt argument).
    """
    if reasoning not in ('low', 'medium', 'high', 'xhigh'):
        raise ValueError(f'unknown reasoning level: {reasoning}')
    result = subprocess.run(
        ['codex', 'exec',
         '-c', f'model_reasoning_effort="{reasoning}"',
         '--skip-git-repo-check',
         '-'],
        input=prompt,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('pair_dir')
    ap.add_argument('rule')
    ap.add_argument('reasoning', choices=['low', 'medium', 'high', 'xhigh'])
    ap.add_argument('--compact', action='store_true',
                    help='Trim extras.diff to a short head excerpt (for rules '
                         'with large embedded diffs like quests/gamelogic)')
    ap.add_argument('--max-tokens', type=int, default=None,
                    help='Split the prompt into chunks each under this many '
                         'tokens (estimated). One Codex call per chunk. '
                         'Outputs get a _<n> suffix when chunked.')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print per-chunk sizes and counts, but do not call Codex.')
    args = ap.parse_args()

    pair_dir = Path(args.pair_dir)
    if not (pair_dir / f'{args.rule}.json').exists():
        sys.exit(f'missing rule output: {pair_dir}/{args.rule}.json')

    prompts = build_prompts(pair_dir, args.rule, compact=args.compact,
                            max_tokens=args.max_tokens)
    suffix = '_compact' if args.compact else ''

    for i, prompt in enumerate(prompts):
        chunk_tag = f'_chunk{i+1}of{len(prompts)}' if len(prompts) > 1 else ''
        print(f'Chunk {i+1}/{len(prompts)}: {len(prompt)} chars '
              f'(~{len(prompt) // 4} tokens)')
        if args.dry_run:
            continue
        print(f'Running codex at reasoning={args.reasoning}...')
        response = run_codex(prompt, args.reasoning)
        out_path = (pair_dir /
                    f'llm_{args.rule}{suffix}{chunk_tag}_{args.reasoning}.md')
        out_path.write_text(response)
        print(f'Wrote {out_path} ({len(response)} chars)')


if __name__ == '__main__':
    main()
