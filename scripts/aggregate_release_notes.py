#!/usr/bin/env python3
"""Aggregate per-chunk LLM release-notes into per-rule and top-level docs.

Two-stage aggregation:

1. Per-rule: for each rule, combine its per-chunk markdown files
   (e.g. llm_quests_chunk1of15_xhigh.md ... chunk15of15_xhigh.md) into
   one rule-level markdown.

2. Top-level: combine all per-rule markdowns into one themed
   release-notes document.

Both stages use a tree-reduce that guarantees every LLM call stays under
the given --max-tokens budget. If inputs exceed the budget, they're
packed into batches, each batch aggregated, and the batch outputs
aggregated recursively. That means this works with weaker LLMs too —
the chunk budget controls what fits in one call regardless of input size.

Usage:
    python3 scripts/aggregate_release_notes.py output/8.00H4_9.00B6 xhigh
    python3 scripts/aggregate_release_notes.py <pair_dir> <level> \\
        [--max-tokens 24000] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.release_notes_llm import run_codex  # noqa: E402


RULES = [
    'shields', 'missiles', 'engines', 'weapons', 'turrets',
    'equipment', 'wares', 'ships', 'storage', 'sectors',
    'factions', 'stations', 'jobs', 'loadouts', 'gamestarts',
    'unlocks', 'drops', 'cosmetics', 'quests', 'gamelogic',
]


RULE_AGGREGATE_PROMPT = """\
You are combining partial release-notes summaries for the `{rule}` rule
in X4 Foundations. Each part covers a different slice of the same rule's
output for version {old_version} -> {new_version}.

Merge the parts below into ONE coherent release-notes section for this
rule. Guidelines:

- Preserve specific details, names, numbers, and concrete callouts.
- Group by theme if the parts already had themes; unify headings.
- Drop duplicates (same point mentioned in multiple parts).
- Keep the writeup tight. Don't add flourish the parts didn't have.
- Plain English. No internal field names like RuleOutput or DiffReport.
- End with a one-sentence summary of what this rule shows overall.

PARTS ({count}):

{parts}

Write the unified section now.
"""


FINAL_AGGREGATE_PROMPT = """\
You are writing the top-level release notes for X4 Foundations going
from version {old_version} to {new_version}. Each section below is the
release-notes writeup for one category of game content (missiles,
ships, quests, etc.).

Combine them into ONE player-facing release-notes document. Guidelines:

- Group by player-facing theme (e.g. Combat, Story & Missions,
  Economy & Stations, World & Factions, Tutorials & Starts, Presentation
  & Other). Pick theme names that fit what the sections actually contain.
- Keep the most specific, concrete, player-visible details from each
  section. Drop fluff and duplicates.
- Short section intros, dense bullet-like prose inside each section.
- Plain English. No rule tag jargon ("weapons rule says..." — just
  write what changed).
- Don't invent facts. If a section didn't say something, don't add it.
- Open with a one-paragraph overview that frames the release.
- Close with a one-sentence takeaway.

SECTIONS ({count}):

{parts}

Write the release notes now.
"""


SUBBATCH_PROMPT = """\
You are merging partial release notes for X4 Foundations going from
version {old_version} to {new_version}. This is an intermediate merge —
downstream passes will merge your output with other intermediate merges.

Combine the parts below into ONE partial merge. Preserve specific
details and named callouts; drop duplicates; keep structure consistent.
Do NOT add a final summary line; another pass will write that.

PARTS ({count}):

{parts}

Write the merged partial now.
"""


def _est_tokens(s: str) -> int:
    return len(s) // 4


def _pack_into_batches(items: list[str], budget: int, overhead: int
                       ) -> list[list[str]]:
    """Greedy-pack items into batches where each batch's combined size
    plus prompt overhead stays under `budget`. If a single item exceeds
    the budget on its own, it gets its own batch (and may still fail at
    the LLM — caller should pick a bigger budget in that case).
    """
    batches: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for item in items:
        item_size = _est_tokens(item)
        if current and current_size + item_size + overhead > budget:
            batches.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += item_size
    if current:
        batches.append(current)
    return batches


def _render_parts(parts: list[str]) -> str:
    return '\n\n---\n\n'.join(f'## Part {i+1}\n\n{p.strip()}'
                              for i, p in enumerate(parts))


def _build_prompt(template: str, parts: list[str], **ctx) -> str:
    return template.format(count=len(parts),
                           parts=_render_parts(parts),
                           **ctx)


def tree_reduce(parts: list[str], template: str, budget: int,
                reasoning: str, dry_run: bool, ctx: dict,
                label: str = '') -> str:
    """Recursively merge `parts` using `template` until a single string
    remains. Each LLM call stays under `budget` tokens including prompt
    overhead.
    """
    empty_prompt = _build_prompt(template, [], **ctx)
    overhead = _est_tokens(empty_prompt)

    # Base case: all parts fit in one call.
    single = _build_prompt(template, parts, **ctx)
    if _est_tokens(single) <= budget:
        print(f'  {label}: 1 call  ({_est_tokens(single)} tokens, '
              f'{len(parts)} parts)')
        if dry_run:
            return f'<dry-run merge of {len(parts)} parts>'
        return run_codex(single, reasoning)

    # Recursive case: batch and merge, then aggregate batch outputs.
    batches = _pack_into_batches(parts, budget, overhead)
    if len(batches) == 1:
        # Single item too big — best effort, call anyway.
        print(f'  {label}: 1 oversized call  ({_est_tokens(single)} tokens)')
        if dry_run:
            return f'<dry-run oversized merge>'
        return run_codex(single, reasoning)
    print(f'  {label}: tree-reduce  ({len(parts)} parts -> '
          f'{len(batches)} batches)')
    batch_outputs = []
    for i, batch in enumerate(batches):
        sub_label = f'{label} batch {i+1}/{len(batches)}'
        # Intermediate batches use the SUBBATCH prompt so the final merge
        # gets clean partial inputs without pre-written summaries.
        if len(batches) > 1:
            sub_prompt = _build_prompt(SUBBATCH_PROMPT, batch, **ctx)
            if _est_tokens(sub_prompt) > budget:
                # Recurse on sub-batch.
                batch_outputs.append(
                    tree_reduce(batch, SUBBATCH_PROMPT, budget, reasoning,
                                dry_run, ctx, label=sub_label))
            else:
                print(f'    {sub_label}: 1 call '
                      f'({_est_tokens(sub_prompt)} tokens, {len(batch)} parts)')
                if dry_run:
                    batch_outputs.append(f'<dry-run partial {i+1}>')
                else:
                    batch_outputs.append(run_codex(sub_prompt, reasoning))
    # Final merge over batch outputs using the original template.
    return tree_reduce(batch_outputs, template, budget, reasoning,
                       dry_run, ctx, label=f'{label} final')


def collect_rule_chunks(pair_dir: Path, rule: str, level: str) -> list[Path]:
    """Return this rule's per-chunk markdown files (or the single file if
    no chunking happened). Excludes the _aggregated.md output if re-run.
    """
    single = pair_dir / f'llm_{rule}_{level}.md'
    chunk_pattern = re.compile(
        rf'^llm_{re.escape(rule)}_chunk\d+of\d+_{re.escape(level)}\.md$')
    chunks = sorted(p for p in pair_dir.iterdir()
                    if chunk_pattern.match(p.name))
    if chunks:
        return chunks
    if single.exists():
        return [single]
    return []


def aggregate_rule(pair_dir: Path, rule: str, level: str, budget: int,
                   reasoning: str, dry_run: bool, versions: tuple[str, str]
                   ) -> tuple[Path, str] | None:
    """Aggregate one rule's chunks. Returns (output_path, content_string).
    Content is the merged markdown (or a placeholder in dry-run)."""
    chunks = collect_rule_chunks(pair_dir, rule, level)
    if not chunks:
        return None
    if len(chunks) == 1:
        # Nothing to aggregate; use existing single file as-is.
        return chunks[0], chunks[0].read_text()
    old_v, new_v = versions
    print(f'rule {rule}: aggregating {len(chunks)} chunks')
    parts = [p.read_text() for p in chunks]
    ctx = {'rule': rule, 'old_version': old_v, 'new_version': new_v}
    merged = tree_reduce(parts, RULE_AGGREGATE_PROMPT, budget, reasoning,
                         dry_run, ctx, label=rule)
    out_path = pair_dir / f'llm_{rule}_aggregated_{level}.md'
    if dry_run:
        # Placeholder: concatenate parts so the top-level estimator sees
        # realistic sizes.
        merged = '\n\n'.join(parts)
    else:
        out_path.write_text(merged)
        print(f'  wrote {out_path} ({len(merged)} chars)')
    return out_path, merged


def aggregate_top(pair_dir: Path, rule_outputs: list[tuple[Path, str]],
                  level: str, budget: int, reasoning: str, dry_run: bool,
                  versions: tuple[str, str]) -> Path:
    parts = [content for _, content in rule_outputs]
    old_v, new_v = versions
    ctx = {'old_version': old_v, 'new_version': new_v}
    print(f'top-level: combining {len(rule_outputs)} rule sections')
    merged = tree_reduce(parts, FINAL_AGGREGATE_PROMPT, budget, reasoning,
                         dry_run, ctx, label='top')
    out_path = pair_dir / f'RELEASE_NOTES_{level}.md'
    if not dry_run:
        out_path.write_text(merged)
        print(f'  wrote {out_path} ({len(merged)} chars)')
    return out_path


def parse_versions(pair_dir: Path) -> tuple[str, str]:
    m = re.match(r'^([^_]+)_(.+)$', pair_dir.name)
    if not m:
        raise ValueError(f'cannot parse versions from {pair_dir.name!r}')
    return m.group(1), m.group(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('pair_dir', help='e.g. output/8.00H4_9.00B6')
    ap.add_argument('level', choices=['low', 'medium', 'high', 'xhigh'])
    ap.add_argument('--max-tokens', type=int, default=24000,
                    help='Per-LLM-call token budget (default: 24000).')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print the call plan without invoking Codex.')
    args = ap.parse_args()

    pair_dir = Path(args.pair_dir)
    if not pair_dir.is_dir():
        sys.exit(f'missing pair dir: {pair_dir}')
    versions = parse_versions(pair_dir)

    rule_outputs: list[tuple[Path, str]] = []
    for rule in RULES:
        result = aggregate_rule(pair_dir, rule, args.level, args.max_tokens,
                                args.level, args.dry_run, versions)
        if result is not None:
            rule_outputs.append(result)

    if not rule_outputs:
        sys.exit(f'no per-rule markdowns found for level={args.level}')

    aggregate_top(pair_dir, rule_outputs, args.level, args.max_tokens,
                  args.level, args.dry_run, versions)


if __name__ == '__main__':
    main()
