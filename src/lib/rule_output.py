"""Shared RuleOutput dataclass and Canonical-schema helpers.

Replaces the per-rule RuleOutput redefinitions for all rules.
"""
import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Iterable, Optional


@dataclass
class RuleOutput:
    tag: str
    text: str
    extras: dict = field(default_factory=dict)


def render_sources(old: Optional[Iterable[str]], new: Optional[Iterable[str]]) -> str:
    """Render contributor sets for the text channel.

    Equal sets ⇒ '[a+b]'. Different sets ⇒ '[old_a+old_b→new_a+new_b]'. None
    means "this side doesn't exist" (add/remove) — render only the other side.
    'core' is always explicit when present.
    """
    def _fmt(items):
        return '+'.join(sorted(set(items)))
    if old is None and new is None:
        return ''
    if old is None:
        return f'[{_fmt(new)}]'
    if new is None:
        return f'[{_fmt(old)}]'
    old_set = set(old)
    new_set = set(new)
    if old_set == new_set:
        return f'[{_fmt(old_set)}]'
    return f'[{_fmt(old_set)}→{_fmt(new_set)}]'


def parse_versions(pair_dir) -> tuple[str, str]:
    """Read `(old_version, new_version)` from `<pair_dir>/summary.json`.
    summary.json is written by run_rules.py at stage 1; every later
    stage in the pipeline runs after it, so the file is always present.
    """
    summary = json.loads((pair_dir / 'summary.json').read_text())
    return summary['old_version'], summary['new_version']


def format_row(tag: str, name: str, classifications: list[str],
               sources_label: str, parts: list[str]) -> str:
    """Standard rule-output row text:
        `[tag] name (classification1, classification2) [src1+src2]: a, b, c`
    The classification and source-label clauses are dropped if empty.
    """
    classifications_text = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{tag}] {name}{classifications_text}{src}: {", ".join(parts)}'


_DIAG_KINDS = frozenset(('incomplete', 'warning'))


def is_diagnostic(record: dict) -> bool:
    """Diagnostic records (incomplete sentinels, warnings) carry payloads
    useful for debugging but not for release notes. Filtered out by both
    the LLM chunker and the raw renderer.
    """
    extras = record.get('extras') or {}
    return extras.get('kind') in _DIAG_KINDS


def diagnostic_entity_key(tag: str, text: str) -> tuple:
    """Synthetic entity_key for diagnostic outputs (warnings, incomplete sentinels).

    Stable across runs so snapshots don't thrash. Short hash keeps it compact.
    """
    short = sha256(text.encode('utf-8')).hexdigest()[:12]
    return ('diagnostic', tag, short)
