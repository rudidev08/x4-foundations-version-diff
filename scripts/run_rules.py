#!/usr/bin/env python3
"""Run all 20 rules against a pair of X4 versions and write outputs to disk.

Usage:
    python3 scripts/run_rules.py <old_version> <new_version> [--out DIR]

Example:
    python3 scripts/run_rules.py 8.00H4 9.00B6

Writes one JSON file per rule under <out>/<old>_<new>/<rule>.json plus a
summary.json with counts. Input versions are resolved against x4-data/.

Each output JSON is an array of records:
    {"tag": "<rule>", "text": "...", "extras": {<serializable subset>}}

Non-serializable extras fields (ElementTree Element references, sets, etc.)
are dropped — only primitives, lists, dicts, and tuples survive.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import change_map  # noqa: E402
from src.lib import cache  # noqa: E402


# Rules in a stable order. Pre-existing (shields, missiles) first for
# continuity, then the waves in plan order.
RULES = [
    'shields', 'missiles',
    # Wave 1: ware-driven
    'engines', 'weapons', 'turrets', 'equipment', 'wares',
    # Wave 2: macro-driven
    'ships', 'storage', 'sectors',
    # Wave 3: library entity-diff
    'factions', 'stations', 'jobs', 'loadouts',
    'gamestarts', 'unlocks', 'drops', 'cosmetics',
    # Wave 4: file-level
    'quests', 'gamelogic',
]


def _jsonable(value):
    """Coerce a value to something `json.dumps` can handle, or return a
    placeholder string. Keeps primitives, lists, tuples, dicts (stringifying
    non-string keys), and sets (converted to sorted lists).
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, set):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    # ElementTree Element, Path, anything else — drop to repr.
    return f'<{type(value).__name__}>'


def _serialize_output(out):
    return {
        'tag': out.tag,
        'text': out.text,
        'extras': _jsonable(out.extras),
    }


def run_rule(name: str, old_root: Path, new_root: Path, changes):
    module = importlib.import_module(f'src.rules.{name}')
    # All rules accept (old_root, new_root, changes) — some ignore changes.
    try:
        return module.run(old_root, new_root, changes)
    except TypeError:
        return module.run(old_root, new_root)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('old_version', help='e.g., 8.00H4')
    ap.add_argument('new_version', help='e.g., 9.00B6')
    ap.add_argument('--game-data', default=str(ROOT / 'x4-data'),
                    help='Directory containing the extracted X4 version '
                         'folders (default: ./x4-data).')
    ap.add_argument('--out', default=str(ROOT / 'artifacts'),
                    help='Output directory (default: ./artifacts). Each '
                         'pair creates a <old>_<new>/ subdirectory.')
    ap.add_argument('--only', help='Comma-separated list of rules to run')
    args = ap.parse_args()

    game_data = Path(args.game_data)
    old_root = game_data / args.old_version
    new_root = game_data / args.new_version
    if not old_root.is_dir():
        sys.exit(f'missing game-data version folder: {old_root}')
    if not new_root.is_dir():
        sys.exit(f'missing game-data version folder: {new_root}')

    rules = [r.strip() for r in args.only.split(',')] if args.only else RULES
    for r in rules:
        if r not in RULES:
            sys.exit(f'unknown rule: {r} (valid: {", ".join(RULES)})')

    out_dir = Path(args.out) / f'{args.old_version}_{args.new_version}'
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'{args.old_version} -> {args.new_version}  (writing to {out_dir})')
    print()
    changes = change_map.build(old_root, new_root)
    summary = {
        'old_version': args.old_version,
        'new_version': args.new_version,
        'changed_files': len(changes),
        'rules': {},
    }
    for name in rules:
        cache.clear()
        t0 = time.monotonic()
        outputs = run_rule(name, old_root, new_root, changes)
        elapsed = time.monotonic() - t0
        serialized = [_serialize_output(o) for o in outputs]
        (out_dir / f'{name}.json').write_text(
            json.dumps(serialized, indent=2, ensure_ascii=False, sort_keys=True)
        )
        kinds: dict[str, int] = {}
        for o in outputs:
            k = o.extras.get('kind', 'unspecified') if isinstance(o.extras, dict) else 'unspecified'
            kinds[k] = kinds.get(k, 0) + 1
        summary['rules'][name] = {
            'count': len(outputs),
            'by_kind': kinds,
            'elapsed_seconds': round(elapsed, 2),
        }
        kind_str = ', '.join(f'{k}={v}' for k, v in sorted(kinds.items()))
        print(f'  {name:12s} {len(outputs):5d} outputs  ({elapsed:5.1f}s)  [{kind_str}]')

    (out_dir / 'summary.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True)
    )
    total = sum(r['count'] for r in summary['rules'].values())
    print()
    print(f'{total} total outputs across {len(rules)} rules.')
    print(f'Summary: {out_dir / "summary.json"}')


if __name__ == '__main__':
    main()
