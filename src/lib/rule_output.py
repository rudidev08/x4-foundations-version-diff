"""Shared RuleOutput dataclass and Canonical-schema helpers.

Replaces the per-rule RuleOutput redefinitions for new rules. `shields.py` and
`missiles.py` keep their local definitions — not migrated in this wave.
"""
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


def snapshot_line(r: RuleOutput) -> str:
    """One line per output for Tier B snapshots. Deterministic + sort-friendly.

    Format: '<entity_key_repr>\t<kind>\t<subsource>\t<sha256(text)>'
    """
    ek = r.extras.get('entity_key')
    kind = r.extras.get('kind', '')
    subsource = r.extras.get('subsource', '')
    digest = sha256(r.text.encode('utf-8')).hexdigest()
    return f'{repr(ek)}\t{kind}\t{subsource}\t{digest}'


def diagnostic_entity_key(tag: str, text: str) -> tuple:
    """Synthetic entity_key for diagnostic outputs (warnings, incomplete sentinels).

    Stable across runs so snapshots don't thrash. Short hash keeps it compact.
    """
    short = sha256(text.encode('utf-8')).hexdigest()[:12]
    return ('diagnostic', tag, short)
