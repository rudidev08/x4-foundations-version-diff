#!/usr/bin/env python3
"""Pure planning / prompt-prep engine for the interactive /x4-diff skill.

Why this exists
    The batch pipeline (run.sh) shells out to an external LLM (LLM_CMD in
    .env). The interactive audience — players with only a Claude Code
    subscription, no API key or local model — has no such LLM, so generation
    must run as in-session Claude subagents. That inverts the control flow:
    instead of a Python driver calling an LLM, the /x4-diff *skill* drives and
    calls this module for the deterministic work, then dispatches a subagent
    for every LLM step.

Reuse boundary
    Reuses the batch pipeline's *pure* pieces unchanged — release_notes_llm.
    build_prompts (stage-2 chunking), the aggregate_release_notes merge
    templates + _build_prompt, llm_budget.pack_into_batches / est_tokens,
    collect_rule_chunks — and re-expresses aggregate_release_notes.tree_reduce
    as skill-driven "rounds". It replaces only the LLM *execution* (run_llm /
    LLM_CMD) with subagents; the batch scripts are not modified. Importing
    release_notes_llm runs its _load_env() once, but every input here is
    passed explicitly (--game-data, --out, --max-tokens, the tag-derived
    pair_dir), so .env can never affect an interactive run.

Contract
    No LLM calls. Renders prompts under <pair_dir>/_work and
    <pair_dir>/.treereduce; prints exactly one JSON object on stdout (progress,
    if any, to stderr); on bad input exits non-zero with a one-line "error:"
    message, never a traceback.

No missing data (enforced in code, not by trusting the driver)
    - _validated_chunks: a chunked rule merges only if exactly one count M is
      present with all M files — a skipped chunk or a budget change (two
      chunkings on disk) is a hard stop.
    - _coverage_gate: 'top' refuses unless every changed, non-diagnostic rule
      (derived from summary.json) produced non-empty output — on the first
      call AND every resume. --allow-missing is the only, surfaced, way to omit
      one.
    - _fingerprint: resume is content-sensitive — if a scope's leaf inputs
      changed (a rule regenerated, re-chunked, or un-skipped), the stale merge
      is rebuilt, not served as done.

Subcommands
    tag <name>                          -> {"tag": "<slug>"}
    prep-rule <pair_dir> <rule> --max-tokens N [--compact]
    plan-round <scope> <pair_dir> --max-tokens N [--out <final>] [--allow-missing <rules>]
        scope = "rule:<name>" or "top"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.release_notes_llm import build_prompts            # noqa: E402
from scripts.aggregate_release_notes import (                  # noqa: E402
    RULE_AGGREGATE_PROMPT, FINAL_AGGREGATE_PROMPT, SUBBATCH_PROMPT,
    _build_prompt, collect_rule_chunks,
)
from scripts.run_rules import RULES                            # noqa: E402
from src.lib.llm_budget import est_tokens, pack_into_batches   # noqa: E402
from src.lib.rule_output import is_diagnostic, parse_versions  # noqa: E402


def slugify_tag(name: str) -> str:
    """Lowercase a model id and replace filesystem-hostile characters so it
    is safe as a path segment (e.g. 'claude-opus-4-8[1m]' has '[' ']' glob
    metacharacters). Anything outside [a-z0-9._-] becomes '-', repeats
    collapse, leading/trailing '-' are stripped; empty falls back.
    """
    s = re.sub(r'[^a-z0-9._-]+', '-', name.strip().lower())
    s = re.sub(r'-{2,}', '-', s).strip('-')
    return s or 'claude-session'


def prep_rule(pair_dir: Path, rule: str, budget: int, compact: bool = False) -> dict:
    """Render the per-chunk prompts for one rule and report a manifest.

    Reuses `build_prompts` (which drops diagnostic records and chunks to
    `budget`). Writes each pending chunk's prompt under `<pair_dir>/_work/`
    and reproduces release_notes_llm's output naming so the existing merge
    stage finds the files. Pure except for those scratch writes.
    """
    pair_dir = Path(pair_dir)
    records = json.loads((pair_dir / f'{rule}.json').read_text())
    dropped = sum(1 for r in records if is_diagnostic(r))
    prompts = build_prompts(pair_dir, rule, compact=compact, max_tokens=budget)
    work = pair_dir / '_work'
    suffix = '_compact' if compact else ''
    n = len(prompts)
    chunks = []
    for i, prompt in enumerate(prompts):
        out = (pair_dir / f'llm_{rule}{suffix}.md' if n == 1
               else pair_dir / f'llm_{rule}{suffix}_chunk{i + 1}of{n}.md')
        done = out.exists() and out.stat().st_size > 0
        prompt_path = None
        if not done:
            work.mkdir(parents=True, exist_ok=True)
            prompt_path = work / f'{out.stem}.prompt.txt'
            prompt_path.write_text(prompt)
        tok = est_tokens(prompt)
        chunks.append({
            'prompt_path': str(prompt_path) if prompt_path else None,
            'out_path': str(out),
            'est_tokens': tok,
            'oversized': tok > budget,
            'done': done,
        })
    return {
        'rule': rule,
        'chunks': chunks,
        'empty_after_diagnostic': len(records) > 0 and n == 0,
        'dropped_diagnostic': dropped,
    }


def _nonempty(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 0


def _scope_key(scope: str) -> str:
    return scope.replace(':', '_')


def _scope_template_ctx(scope: str, pair_dir: Path):
    old, new = parse_versions(pair_dir)
    if scope.startswith('rule:'):
        return RULE_AGGREGATE_PROMPT, {
            'rule': scope.split(':', 1)[1], 'old_version': old, 'new_version': new}
    return FINAL_AGGREGATE_PROMPT, {'old_version': old, 'new_version': new}


def _result_path(scope: str, pair_dir: Path, final_out) -> Path:
    if scope.startswith('rule:'):
        return pair_dir / f"llm_{scope.split(':', 1)[1]}_aggregated.md"
    if not final_out:
        raise SystemExit('plan-round top requires --out <final_path>')
    return Path(final_out)


def _validated_chunks(pair_dir: Path, rule: str) -> list[Path]:
    """`collect_rule_chunks` plus a completeness check the batch helper omits: a
    chunked rule must have exactly one declared count M with all M files present.

    Catches a skipped chunk (present count < M) and a budget change that left two
    chunkings on disk (multiple M values) — either would otherwise be merged as
    if complete, silently holing or duplicating the rule.
    """
    chunks = collect_rule_chunks(pair_dir, rule)
    declared = set()
    for p in chunks:
        m = re.search(r'_chunk\d+of(\d+)\.md$', p.name)
        if m:
            declared.add(int(m.group(1)))
    if len(declared) > 1:
        raise SystemExit(
            f'rule {rule}: chunk files for multiple counts {sorted(declared)} on '
            f"disk (budget changed mid-run?); delete this rule's chunk files and "
            f'regenerate')
    if declared:
        (m,) = declared
        if len(chunks) != m:
            raise SystemExit(
                f'rule {rule}: {len(chunks)} of {m} chunks present; generate the '
                f"missing chunk(s), or clear this rule's files "
                f'(rm -f llm_{rule}*.md) and pass --allow-missing {rule} to omit it')
    return chunks


def _leaf_parts(scope: str, pair_dir: Path) -> list[Path]:
    """Return the input parts for one merge round.

    For the 'top' scope, assumes every changed multi-chunk rule has already
    been finalized (its llm_<rule>_aggregated.md exists), and raises otherwise
    to avoid dropping a rule.
    """
    if scope.startswith('rule:'):
        return _validated_chunks(pair_dir, scope.split(':', 1)[1])
    parts = []
    for rule in RULES:
        chunks = _validated_chunks(pair_dir, rule)
        if not chunks:
            continue
        if len(chunks) == 1:
            parts.append(chunks[0])
        else:
            agg = pair_dir / f'llm_{rule}_aggregated.md'
            if not _nonempty(agg):
                raise SystemExit(
                    f'top: rule {rule} has {len(chunks)} chunks but no aggregate '
                    f'yet; finalize rule:{rule} before top')
            parts.append(agg)
    return parts


def _expected_rules(pair_dir: Path) -> set:
    """Rules that should produce notes: changed AND with at least one
    non-diagnostic record (build_prompts drops diagnostics). Derived from
    stage-1 summary.json's per-rule by_kind counts.
    """
    summary = json.loads((pair_dir / 'summary.json').read_text())
    expected = set()
    for name, info in summary.get('rules', {}).items():
        bk = info.get('by_kind', {})
        non_diag = info.get('count', 0) - bk.get('incomplete', 0) - bk.get('warning', 0)
        if non_diag > 0:
            expected.add(name)
    return expected


def _coverage_gate(pair_dir: Path, allow_missing: set) -> None:
    """Before the top merge, require every expected rule to have produced output
    — deterministic enforcement of "no missing data". A rule may be absent only
    if the caller explicitly allows it (a surfaced, deliberate skip).
    """
    missing = sorted(
        r for r in _expected_rules(pair_dir)
        if r not in allow_missing
        and not any(p.stat().st_size > 0 for p in collect_rule_chunks(pair_dir, r)))
    if missing:
        raise SystemExit(
            'top: these changed rules produced no notes: '
            f'{", ".join(missing)}. Generate them, or pass --allow-missing to omit '
            f'them deliberately (they will be named as omitted).')


def _save_state(state_file: Path, state: dict) -> None:
    state_file.write_text(json.dumps(state))


def _emit_final(work, state_file, result, real_template, ctx, parts_paths,
                round_no, fallback):
    parts = [Path(p).read_text() for p in parts_paths]
    input_tokens = sum(est_tokens(s) for s in parts)
    pf = work / f'r{round_no}_final.prompt.txt'
    pf.write_text(_build_prompt(real_template, parts, **ctx))
    task = {'prompt_path': str(pf), 'out_path': str(result), 'template': 'final'}
    _save_state(state_file, {'round': round_no, 'phase': 'final',
                             'input_tokens': input_tokens, 'tasks': [task]})
    return {'phase': 'final', 'tasks': [task], 'fallback': fallback}


def _plan(work, state_file, result, real_template, ctx, parts_paths, round_no, budget):
    parts = [Path(p).read_text() for p in parts_paths]
    single = _build_prompt(real_template, parts, **ctx)
    if est_tokens(single) <= budget:
        return _emit_final(work, state_file, result, real_template, ctx,
                           parts_paths, round_no, fallback=False)
    overhead = est_tokens(_build_prompt(real_template, [], **ctx))
    batches = pack_into_batches(parts, budget, overhead)
    if len(batches) == 1:
        # one indivisible over-budget part: finalize under the real template
        # (matches aggregate_release_notes.py:174-179), never SUBBATCH.
        return _emit_final(work, state_file, result, real_template, ctx,
                           parts_paths, round_no, fallback=True)
    tasks = []
    for i, batch in enumerate(batches):
        pf = work / f'r{round_no}_b{i}.prompt.txt'
        pf.write_text(_build_prompt(SUBBATCH_PROMPT, batch, **ctx))
        tasks.append({'prompt_path': str(pf),
                      'out_path': str(work / f'r{round_no}_b{i}.md'),
                      'template': 'subbatch'})
    input_tokens = sum(est_tokens(s) for s in parts)
    _save_state(state_file, {'round': round_no, 'phase': 'merge',
                             'input_tokens': input_tokens, 'tasks': tasks})
    return {'phase': 'merge', 'tasks': tasks, 'fallback': False}


def _fingerprint(scope: str, pair_dir: Path, parts: list) -> str:
    """Stable signature of a scope's leaf inputs (name, size, mtime). If any
    leaf changes between runs — a rule regenerated, re-chunked, or un-skipped —
    a persisted merge is stale and must be rebuilt rather than served.
    """
    return json.dumps(sorted([p.name, p.stat().st_size, p.stat().st_mtime_ns]
                             for p in parts))


def plan_round(scope: str, pair_dir, budget: int, final_out=None,
               allow_missing=None) -> dict:
    """Compute ONE merge round for `scope` ('rule:<name>' or 'top').

    Picks one of three shapes (fit-in-one / split-into-batches / lone-
    oversized), writes the round's prompt files, and records the round in
    round_state.json so the next call can detect completion, apply the
    no-shrinkage guard, and advance — owning termination itself.

    For 'top', the coverage gate runs on EVERY call (first and resume), so a
    dropped --allow-missing or a newly-missing rule is caught even mid-run.
    Resume is content-sensitive: if a scope's leaf inputs changed since it was
    planned (a rule regenerated / re-chunked / un-skipped), the stale merge
    state is discarded and the scope is rebuilt rather than served as `done`.
    """
    pair_dir = Path(pair_dir)
    allow_missing = set(allow_missing or [])
    work = pair_dir / '.treereduce' / _scope_key(scope)
    work.mkdir(parents=True, exist_ok=True)
    state_file = work / 'round_state.json'
    fp_file = work / 'leaf_fingerprint.json'
    result = _result_path(scope, pair_dir, final_out)
    real_template, ctx = _scope_template_ctx(scope, pair_dir)

    if not scope.startswith('rule:'):
        _coverage_gate(pair_dir, allow_missing)

    if state_file.exists():
        current_fp = _fingerprint(scope, pair_dir, _leaf_parts(scope, pair_dir))
        if (fp_file.read_text() if fp_file.exists() else '') == current_fp:
            state = json.loads(state_file.read_text())
            pending = [t for t in state['tasks'] if not _nonempty(Path(t['out_path']))]
            if pending:
                return {'phase': state['phase'], 'tasks': pending, 'fallback': False}
            if state['phase'] == 'final':
                return {'phase': 'done', 'final_path': str(result),
                        'tasks': [], 'fallback': False}
            new_parts = [Path(t['out_path']) for t in state['tasks']]
            output_tokens = sum(est_tokens(p.read_text()) for p in new_parts)
            if output_tokens >= state['input_tokens']:
                return _emit_final(work, state_file, result, real_template, ctx,
                                   new_parts, state['round'] + 1, fallback=True)
            return _plan(work, state_file, result, real_template, ctx,
                         new_parts, state['round'] + 1, budget)
        # leaf inputs changed since this scope was planned -> rebuild from scratch
        for stale in work.glob('r[0-9]*'):
            stale.unlink()
        state_file.unlink()

    parts_paths = _leaf_parts(scope, pair_dir)
    if not scope.startswith('rule:') and not parts_paths:
        raise SystemExit('top: no rule sections to merge '
                         '(nothing changed, or all changes were diagnostic)')
    if scope.startswith('rule:') and len(parts_paths) <= 1:
        final = str(parts_paths[0]) if parts_paths else None
        return {'phase': 'done', 'final_path': final, 'tasks': [], 'fallback': False}
    fp_file.write_text(_fingerprint(scope, pair_dir, parts_paths))
    return _plan(work, state_file, result, real_template, ctx, parts_paths, 0, budget)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_tag = sub.add_parser('tag')
    p_tag.add_argument('name')

    p_prep = sub.add_parser('prep-rule')
    p_prep.add_argument('pair_dir')
    p_prep.add_argument('rule')
    p_prep.add_argument('-b', '--max-tokens', type=int, required=True)
    p_prep.add_argument('--compact', action='store_true')

    p_round = sub.add_parser('plan-round')
    p_round.add_argument('scope')
    p_round.add_argument('pair_dir')
    p_round.add_argument('-b', '--max-tokens', type=int, required=True)
    p_round.add_argument('-o', '--out', default=None)
    p_round.add_argument('--allow-missing', default='',
                         help='comma-separated rules allowed to be absent from '
                              'the top merge (deliberate, surfaced skips)')

    args = ap.parse_args()
    try:
        if args.cmd == 'tag':
            result = {'tag': slugify_tag(args.name)}
        elif args.cmd == 'prep-rule':
            result = prep_rule(Path(args.pair_dir), args.rule, args.max_tokens,
                               args.compact)
        else:
            allow = [r.strip() for r in args.allow_missing.split(',') if r.strip()]
            result = plan_round(args.scope, args.pair_dir, args.max_tokens,
                                args.out, allow_missing=allow)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        sys.exit(f'error: {e}')
    json.dump(result, sys.stdout)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
