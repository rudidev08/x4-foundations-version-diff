#!/usr/bin/env python3
"""Aggregate per-chunk LLM release-notes into per-rule and top-level docs.

Two-stage aggregation:

1. Per-rule: for each rule, combine its per-chunk markdown files
   (e.g. llm_quests_chunk1of15.md ... chunk15of15.md) into one
   rule-level markdown.

2. Top-level: combine all per-rule markdowns into one themed
   release-notes document.

Both stages use a tree-reduce that guarantees every LLM call stays under
the given --max-tokens budget. If inputs exceed the budget, they're
packed into batches, each batch aggregated, and the batch outputs
aggregated recursively. That means this works with weaker LLMs too —
the chunk budget controls what fits in one call regardless of input size.

Usage:
    python3 scripts/aggregate_release_notes.py <pair_dir> --model NAME \\
        [--max-tokens 24000] [--dry-run]

Per-rule aggregates stay under `<pair_dir>/`; the top-level
`<old>-<new>-<MODEL>.md` is written to `output/` as a deliverable.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.release_notes_llm import (  # noqa: E402
    run_llm, resolve_profile, resolve_max_tokens,
)
from scripts.run_rules import RULES  # noqa: E402
from src.lib.llm_budget import est_tokens, pack_into_batches  # noqa: E402
from src.lib.rule_output import parse_versions  # noqa: E402


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
- Keep any "added but disabled" / feature-gated language intact — do
  not sand it down to "new feature" when the source part flagged a
  gate. Same for modding API / "Under the Hood" callouts.
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
- If sections cite before/after numbers, keep them in the merged
  output. Don't replace "hull 24000→48000" with "much tougher".
- If any section flagged a feature as added-but-disabled or gated,
  preserve that framing. Also keep any "Under the Hood" / modding API
  callouts as a dedicated section so mod authors see them.
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


_FALLBACKS: list[str] = []  # labels where tree_reduce hit the no-shrinkage guard


def _render_parts(parts: list[str]) -> str:
    return '\n\n---\n\n'.join(f'## Part {i+1}\n\n{p.strip()}'
                              for i, p in enumerate(parts))


def _build_prompt(template: str, parts: list[str], **ctx) -> str:
    return template.format(count=len(parts),
                           parts=_render_parts(parts),
                           **ctx)


def _call_or_cache(prompt: str, profile: dict, cache_dir: Path,
                   label: str) -> str:
    """Run the LLM, persisting the response keyed by prompt hash so reruns
    after a partial failure skip already-computed merges.

    Cache key is the SHA-256 of the prompt text — any change to template,
    parts, or context invalidates the entry. Empty responses aren't
    cached (treated as a failed call worth retrying).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16]
    cached = cache_dir / f'{key}.md'
    if cached.exists() and cached.stat().st_size > 0:
        print(f'    {label}: cached ({cached.name})')
        return cached.read_text()
    result = run_llm(prompt, profile=profile)
    cached.write_text(result)
    return result


def tree_reduce(parts: list[str], template: str, budget: int,
                profile: dict, dry_run: bool, ctx: dict, cache_dir: Path,
                label: str = '') -> str:
    """Recursively merge `parts` using `template` until a single string
    remains. Each LLM call stays under `budget` tokens including prompt
    overhead, and every call's response is persisted under `cache_dir`
    so a failed run resumes from the last completed batch.
    """
    empty_prompt = _build_prompt(template, [], **ctx)
    overhead = est_tokens(empty_prompt)

    single = _build_prompt(template, parts, **ctx)
    if est_tokens(single) <= budget:
        print(f'  {label}: 1 call  ({est_tokens(single)} tokens, '
              f'{len(parts)} parts)')
        if dry_run:
            return f'<dry-run merge of {len(parts)} parts>'
        return _call_or_cache(single, profile, cache_dir, label)

    batches = pack_into_batches(parts, budget, overhead)
    if len(batches) == 1:
        print(f'  {label}: 1 oversized call  ({est_tokens(single)} tokens)')
        if dry_run:
            return '<dry-run oversized merge>'
        return _call_or_cache(single, profile, cache_dir, label)
    print(f'  {label}: tree-reduce  ({len(parts)} parts -> '
          f'{len(batches)} batches)')
    batch_outputs = []
    for i, batch in enumerate(batches):
        sub_label = f'{label} batch {i+1}/{len(batches)}'
        # Intermediate batches use the SUBBATCH prompt so the final merge
        # gets clean partial inputs without pre-written summaries.
        sub_prompt = _build_prompt(SUBBATCH_PROMPT, batch, **ctx)
        if est_tokens(sub_prompt) > budget:
            batch_outputs.append(
                tree_reduce(batch, SUBBATCH_PROMPT, budget,
                            profile, dry_run, ctx, cache_dir,
                            label=sub_label))
        else:
            print(f'    {sub_label}: 1 call '
                  f'({est_tokens(sub_prompt)} tokens, {len(batch)} parts)')
            if dry_run:
                batch_outputs.append(f'<dry-run partial {i+1}>')
            else:
                batch_outputs.append(
                    _call_or_cache(sub_prompt, profile, cache_dir,
                                   sub_label))
    # Progress guard. If the LLM preserved everything (batch outputs as big
    # as inputs), recursing won't converge — pack_into_batches will keep
    # producing the same shape and we loop until the stack blows. Detect
    # no-shrinkage and fall back to one oversized call under the original
    # template; the LLM may truncate, but that beats hanging. Fallbacks are
    # recorded for a single end-of-run summary instead of alarming mid-run.
    parts_tokens = sum(est_tokens(p) for p in parts)
    output_tokens = sum(est_tokens(b) for b in batch_outputs)
    if not dry_run and output_tokens >= parts_tokens:
        final_prompt = _build_prompt(template, batch_outputs, **ctx)
        _FALLBACKS.append(label)
        return _call_or_cache(final_prompt, profile, cache_dir,
                              f'{label} final')
    # Final merge over batch outputs using the original template.
    return tree_reduce(batch_outputs, template, budget, profile,
                       dry_run, ctx, cache_dir, label=f'{label} final')


def collect_rule_chunks(pair_dir: Path, rule: str) -> list[Path]:
    """Return this rule's per-chunk markdown files (or the single file if
    no chunking happened). Excludes the _aggregated.md output if re-run.

    Compact and non-compact outputs are mutually exclusive. If both flavors
    exist in the same directory, raises — caller must clean up the stale set.
    """
    normal_re = re.compile(
        rf'^llm_{re.escape(rule)}_chunk\d+of\d+\.md$')
    compact_re = re.compile(
        rf'^llm_{re.escape(rule)}_compact_chunk\d+of\d+\.md$')
    normal = sorted(p for p in pair_dir.iterdir() if normal_re.match(p.name))
    compact = sorted(p for p in pair_dir.iterdir() if compact_re.match(p.name))
    if normal and compact:
        raise RuntimeError(
            f'rule {rule}: both compact and non-compact chunk outputs exist '
            f'in {pair_dir}; delete one set before aggregating')
    chunks = normal or compact
    if chunks:
        return chunks
    single_normal = pair_dir / f'llm_{rule}.md'
    single_compact = pair_dir / f'llm_{rule}_compact.md'
    if single_normal.exists() and single_compact.exists():
        raise RuntimeError(
            f'rule {rule}: both compact and non-compact single-file outputs '
            f'exist in {pair_dir}; delete one before aggregating')
    if single_normal.exists():
        return [single_normal]
    if single_compact.exists():
        return [single_compact]
    return []


def aggregate_rule(pair_dir: Path, rule: str, budget: int,
                   profile: dict, dry_run: bool,
                   versions: tuple[str, str]
                   ) -> tuple[Path, str] | None:
    """Aggregate one rule's chunks. Returns (output_path, content_string).
    Content is the merged markdown (or a placeholder in dry-run).

    Resumable: if the aggregated file already exists on disk, return it
    without making any LLM calls.
    """
    chunks = collect_rule_chunks(pair_dir, rule)
    if not chunks:
        return None
    if len(chunks) == 1:
        # Nothing to aggregate; use existing single file as-is.
        return chunks[0], chunks[0].read_text()
    out_path = pair_dir / f'llm_{rule}_aggregated.md'
    if out_path.exists() and out_path.stat().st_size > 0 and not dry_run:
        print(f'rule {rule}: skip (cached {out_path.name})')
        return out_path, out_path.read_text()
    old_v, new_v = versions
    print(f'rule {rule}: aggregating {len(chunks)} chunks')
    parts = [p.read_text() for p in chunks]
    ctx = {'rule': rule, 'old_version': old_v, 'new_version': new_v}
    cache_dir = pair_dir / '.treereduce' / f'rule_{rule}'
    merged = tree_reduce(parts, RULE_AGGREGATE_PROMPT, budget,
                         profile, dry_run, ctx, cache_dir, label=rule)
    if dry_run:
        # Placeholder: concatenate parts so the top-level estimator sees
        # realistic sizes.
        merged = '\n\n'.join(parts)
    else:
        out_path.write_text(merged)
        print(f'  wrote {out_path} ({len(merged)} chars)')
    return out_path, merged


def aggregate_top(pair_dir: Path, rule_outputs: list[tuple[Path, str]],
                  tag: str, budget: int, profile: dict, dry_run: bool,
                  versions: tuple[str, str]) -> Path:
    # Final release notes land under <project_root>/output/ as a
    # deliverable, separated from the regeneratable artifacts/ tree.
    old_v, new_v = versions
    output_dir = ROOT / 'output'
    out_path = output_dir / f'{old_v}-{new_v}-{tag}.md'
    if out_path.exists() and out_path.stat().st_size > 0 and not dry_run:
        print(f'top-level: skip (cached {out_path})')
        return out_path
    parts = [content for _, content in rule_outputs]
    ctx = {'old_version': old_v, 'new_version': new_v}
    print(f'top-level: combining {len(rule_outputs)} rule sections')
    cache_dir = pair_dir / '.treereduce' / 'top'
    merged = tree_reduce(parts, FINAL_AGGREGATE_PROMPT, budget,
                         profile, dry_run, ctx, cache_dir, label='top')
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(merged)
        print(f'  wrote {out_path} ({len(merged)} chars)')
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument('pair_dir',
                        help='e.g. artifacts/8.00H4-9.00B6-opus-4.7-max')
    parser.add_argument('--model', required=True,
                        help='Active LLM profile (matches a *_MODEL_NAME '
                             'entry in .env).')
    advanced = parser.add_argument_group(
        'advanced (rarely needed — defaults are usually correct)')
    advanced.add_argument('--max-tokens', type=int, default=None,
                          help='Per-LLM-call token budget. Overrides the '
                               '.env profile\'s CHUNK_KB and '
                               'X4_LLM_MAX_TOKENS.')
    advanced.add_argument('--dry-run', action='store_true',
                          help='Print the call plan without invoking the LLM.')
    args = parser.parse_args()

    pair_dir = Path(args.pair_dir)
    if not pair_dir.is_dir():
        sys.exit(f'missing pair dir: {pair_dir}')
    versions = parse_versions(pair_dir)

    profile = resolve_profile(args.model)
    tag = profile['MODEL_NAME']
    max_tokens = resolve_max_tokens(args.max_tokens, profile)

    rule_outputs: list[tuple[Path, str]] = []
    for rule in RULES:
        result = aggregate_rule(pair_dir, rule, max_tokens,
                                profile, args.dry_run, versions)
        if result is not None:
            rule_outputs.append(result)

    if not rule_outputs:
        sys.exit(f'no per-rule markdowns found in {pair_dir}')

    aggregate_top(pair_dir, rule_outputs, tag, max_tokens,
                  profile, args.dry_run, versions)

    if _FALLBACKS:
        print(f'\nnote: {len(_FALLBACKS)} merge(s) fell back to a single '
              f'oversized call (model preserved detail faster than the '
              f'budget could compress it): {", ".join(_FALLBACKS)}')


if __name__ == '__main__':
    main()
