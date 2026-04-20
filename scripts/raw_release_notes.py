#!/usr/bin/env python3
"""Render the deterministic per-rule output as a single markdown doc.

Reads every rule's JSON from `<pair_dir>/<rule>.json` (produced by
run_rules.py) and concatenates the player-facing `text` fields, grouped
by classification. No LLM calls. The result is the unfiltered, fully
detailed change list — useful as a sanity check against the LLM-written
release notes and as a fallback when LLM output is unsatisfying.

Usage:
    python3 scripts/raw_release_notes.py <pair_dir> --model NAME

Writes:
    output/<old>-<new>-<MODEL>-raw.md

The `--model` tag makes the filename unique per pipeline run so
parallel runs on different models never share files, even though the
raw content itself is model-independent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.run_rules import RULES  # noqa: E402
from src.lib.rule_output import is_diagnostic, parse_versions  # noqa: E402


def _group_key(record: dict) -> str:
    extras = record.get('extras') or {}
    classifications = extras.get('classifications') or []
    return str(classifications[0]) if classifications else 'general'


def _render_rule(rule: str, records: list[dict]) -> str:
    """One rule section: header, then sub-headers per classification group,
    then a bullet per record's `text` field. Records with the same primary
    classification group together; groups are alphabetized for stability.
    """
    real = [r for r in records if not is_diagnostic(r)]
    if not real:
        return ''
    groups: dict[str, list[dict]] = {}
    for r in real:
        groups.setdefault(_group_key(r), []).append(r)
    lines = [f'## {rule} ({len(real)} records)']
    for key in sorted(groups):
        group = groups[key]
        lines.append(f'\n### {key} ({len(group)})\n')
        for r in group:
            text = (r.get('text') or '').strip()
            if text:
                lines.append(f'- {text}')
    return '\n'.join(lines) + '\n'


def render(pair_dir: Path) -> str:
    old_v, new_v = parse_versions(pair_dir)
    out = [f'# X4 raw changes: {old_v} → {new_v}\n',
           'Deterministic per-rule output, grouped by primary '
           'classification. No LLM processing.\n']
    for rule in RULES:
        rule_json = pair_dir / f'{rule}.json'
        if not rule_json.exists():
            continue
        records = json.loads(rule_json.read_text())
        section = _render_rule(rule, records)
        if section:
            out.append(section)
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument('pair_dir', help='e.g. artifacts/8.00H4-9.00B6-<model>')
    ap.add_argument('--model', required=True,
                    help='Model tag (matches a *_MODEL_NAME in .env). '
                         'Used only for the output filename.')
    args = ap.parse_args()

    pair_dir = Path(args.pair_dir)
    if not pair_dir.is_dir():
        sys.exit(f'missing pair dir: {pair_dir}')
    old_v, new_v = parse_versions(pair_dir)

    output_dir = ROOT / 'output'
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f'{old_v}-{new_v}-{args.model}-raw.md'
    out_path.write_text(render(pair_dir))
    print(f'wrote {out_path}')


if __name__ == '__main__':
    main()
