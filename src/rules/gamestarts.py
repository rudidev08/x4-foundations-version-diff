"""Gamestarts rule: emit outputs for gamestart definition changes.

Single-source under `libraries/gamestarts.xml` (+ DLC). Each `<gamestart>` is
diffed via entity-level `diff_library` keyed by `@id`. The rule surfaces:

- The `<gamestart>` root attributes (`@name`, `@image`, `@tags`, `@group`,
  `@description`, …) diffed directly.
- A small set of nested singleton children whose own attributes diff as
  `<child>.<attribute> old→new`:
    - `<cutscene>` — cutscene ref + voice.
    - `<player>` — starting-character `@macro`, `@money`, `@name`.
    - `<player><ship>` — starting ship macro (nested as `player.ship.<attribute>`).
    - `<universe>` — universe flags (`@ventures`, `@visitors`, …).
- Add/remove lifecycle + tag set changes.

Deep subtree content (inventory lists, factions/relations, blueprints, info
items) is out of scope — the changelog signal is the high-level lifecycle +
root/player/ship/universe shape, not the long-tail of DLC-specific starting
gear.
"""
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.rule_output import RuleOutput, format_row, render_sources


TAG = 'gamestarts'

# Classifications come from `@tags` (whitespace-split). Filter out the generic
# token `gamestart` if a vanilla entry ever uses it; real data doesn't but the

_GENERIC_FILTER = frozenset({'gamestart'})


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit gamestarts rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused (library-driven).
    """
    outputs: list[RuleOutput] = []
    locale_old, locale_new = Locale.build_pair(old_root, new_root, outputs, tag=TAG)

    report = diff_library(
        old_root, new_root, 'libraries/gamestarts.xml', './/gamestart',
        key_fn=lambda e: e.get('id'), key_fn_identity='gamestarts_id',
    )
    for record in report.added:
        outputs.extend(_emit_added(record, locale_new))
    for record in report.removed:
        outputs.extend(_emit_removed(record, locale_old))
    for record in report.modified:
        outputs.extend(_emit_modified(record, locale_old, locale_new))

    forward_incomplete(report, outputs, tag=TAG)
    forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


def _classify(gs: ElementTree.Element) -> list[str]:
    """`@tags` whitespace-tokenized. Generic token `gamestart` filtered."""
    raw = (gs.get('tags') or '').split()
    return [t for t in raw if t and t not in _GENERIC_FILTER]


def _diff_root_attrs(old_gs: ElementTree.Element, new_gs: ElementTree.Element) -> list[str]:
    """Diff the `<gamestart>` element's own attributes. No whitelist."""
    out: list[str] = []
    keys = sorted(set(old_gs.attrib) | set(new_gs.attrib))
    for a in keys:
        old_value = old_gs.get(a)
        new_value = new_gs.get(a)
        if old_value != new_value:
            out.append(f'{a} {old_value}→{new_value}')
    return out


def _diff_singleton_child(old_gs: ElementTree.Element, new_gs: ElementTree.Element,
                          tag: str, label: Optional[str] = None) -> list[str]:
    """Diff one singleton direct-child's own attributes as `<label>.<attribute> old→new`.

    If the child exists on one side only → `<label> added` / `<label> removed`.
    `label` defaults to `tag`.
    """
    lab = label or tag
    old_el = old_gs.find(tag)
    new_el = new_gs.find(tag)
    if old_el is None and new_el is None:
        return []
    if old_el is None:
        return [f'{lab} added']
    if new_el is None:
        return [f'{lab} removed']
    out: list[str] = []
    keys = sorted(set(old_el.attrib) | set(new_el.attrib))
    for a in keys:
        old_value = old_el.get(a)
        new_value = new_el.get(a)
        if old_value != new_value:
            out.append(f'{lab}.{a} {old_value}→{new_value}')
    return out


def _diff_player_ship(old_gs: ElementTree.Element, new_gs: ElementTree.Element) -> list[str]:
    """Diff `<player><ship>` starting-ship attributes as `player.ship.<attribute> old→new`.

    The ship element is a singleton under `<player>` when present (some
    gamestarts launch the player embodied / disembarked with no ship).
    """
    old_player = old_gs.find('player')
    new_player = new_gs.find('player')
    old_ship = old_player.find('ship') if old_player is not None else None
    new_ship = new_player.find('ship') if new_player is not None else None
    if old_ship is None and new_ship is None:
        return []
    if old_ship is None:
        return ['player.ship added']
    if new_ship is None:
        return ['player.ship removed']
    out: list[str] = []
    keys = sorted(set(old_ship.attrib) | set(new_ship.attrib))
    for a in keys:
        old_value = old_ship.get(a)
        new_value = new_ship.get(a)
        if old_value != new_value:
            out.append(f'player.ship.{a} {old_value}→{new_value}')
    return out


def _display(gs: ElementTree.Element, locale: Locale, gid: str) -> str:
    """`@name` → locale lookup via `resolve_attr_ref`; fallback `@id`."""
    return resolve_attr_ref(gs, locale, attribute='name', fallback=gid)


def _emit_added(record, locale_new: Locale) -> list[RuleOutput]:
    gs = record.element
    gid = record.key
    name = _display(gs, locale_new, gid)
    classifications = _classify(gs)
    sources_label = render_sources(None, record.sources)
    parts = ['NEW']
    text = format_row(TAG, name, classifications, sources_label, parts)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': gid,
        'kind': 'added',
        'classifications': classifications,
        'gamestart_id': gid,
        'new_sources': list(record.sources),
        'sources': list(record.sources),
        'source': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_removed(record, locale_old: Locale) -> list[RuleOutput]:
    gs = record.element
    gid = record.key
    name = _display(gs, locale_old, gid)
    classifications = _classify(gs)
    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': gid,
        'kind': 'removed',
        'classifications': classifications,
        'gamestart_id': gid,
        'old_sources': list(record.sources),
        'sources': list(record.sources),
        'source': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_modified(record, locale_old: Locale, locale_new: Locale) -> list[RuleOutput]:
    gid = record.key
    name = _display(record.new, locale_new, gid)
    if name == gid:
        name = _display(record.old, locale_old, gid)
    classifications = _classify(record.new)

    changes: list[str] = []
    # Root-level attributes.
    changes.extend(_diff_root_attrs(record.old, record.new))
    # Nested singletons.
    changes.extend(_diff_singleton_child(record.old, record.new, 'cutscene'))
    changes.extend(_diff_singleton_child(record.old, record.new, 'player'))
    # player/ship is nested one more level down — standalone helper.
    changes.extend(_diff_player_ship(record.old, record.new))
    changes.extend(_diff_singleton_child(record.old, record.new, 'universe'))

    if not changes:
        return []

    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': gid,
        'kind': 'modified',
        'classifications': classifications,
        'gamestart_id': gid,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'source': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })]
