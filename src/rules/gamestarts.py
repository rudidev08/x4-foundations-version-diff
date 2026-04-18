"""Gamestarts rule: emit outputs for gamestart definition changes.

Single-source under `libraries/gamestarts.xml` (+ DLC). Each `<gamestart>` is
diffed via entity-level `diff_library` keyed by `@id`. The rule surfaces:

- The `<gamestart>` root attributes (`@name`, `@image`, `@tags`, `@group`,
  `@description`, …) diffed directly.
- A small set of nested singleton children whose own attributes diff as
  `<child>.<attr> old→new`:
    - `<cutscene>` — cutscene ref + voice.
    - `<player>` — starting-character `@macro`, `@money`, `@name`.
    - `<player><ship>` — starting ship macro (nested as `player.ship.<attr>`).
    - `<universe>` — universe flags (`@ventures`, `@visitors`, …).
- Add/remove lifecycle + tag set changes.

Deep subtree content (inventory lists, factions/relations, blueprints, info
items) is out of scope — the changelog signal is the high-level lifecycle +
root/player/ship/universe shape, not the long-tail of DLC-specific starting
gear.
"""
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'gamestarts'

# Classifications come from `@tags` (whitespace-split). Filter out the generic
# token `gamestart` if a vanilla entry ever uses it; real data doesn't but the
# spec calls it out.
_GENERIC_FILTER = frozenset({'gamestart'})


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit gamestarts rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused (library-driven).
    """
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    report = diff_library(
        old_root, new_root, 'libraries/gamestarts.xml', './/gamestart',
        key_fn=lambda e: e.get('id'), key_fn_identity='gamestarts_id',
    )
    for rec in report.added:
        outputs.extend(_emit_added(rec, loc_new))
    for rec in report.removed:
        outputs.extend(_emit_removed(rec, loc_old))
    for rec in report.modified:
        outputs.extend(_emit_modified(rec, loc_old, loc_new))

    forward_incomplete(report, outputs, tag=TAG)
    forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


# ---------- classification ----------


def _classify(gs: ET.Element) -> list[str]:
    """`@tags` whitespace-tokenized. Generic token `gamestart` filtered."""
    raw = (gs.get('tags') or '').split()
    return [t for t in raw if t and t not in _GENERIC_FILTER]


# ---------- attr diffs ----------


def _diff_root_attrs(old_gs: ET.Element, new_gs: ET.Element) -> list[str]:
    """Diff the `<gamestart>` element's own attributes. No whitelist."""
    out: list[str] = []
    keys = sorted(set(old_gs.attrib) | set(new_gs.attrib))
    for a in keys:
        ov = old_gs.get(a)
        nv = new_gs.get(a)
        if ov != nv:
            out.append(f'{a} {ov}→{nv}')
    return out


def _diff_singleton_child(old_gs: ET.Element, new_gs: ET.Element,
                          tag: str, label: Optional[str] = None) -> list[str]:
    """Diff one singleton direct-child's own attributes as `<label>.<attr> old→new`.

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
        ov = old_el.get(a)
        nv = new_el.get(a)
        if ov != nv:
            out.append(f'{lab}.{a} {ov}→{nv}')
    return out


def _diff_player_ship(old_gs: ET.Element, new_gs: ET.Element) -> list[str]:
    """Diff `<player><ship>` starting-ship attrs as `player.ship.<attr> old→new`.

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
        ov = old_ship.get(a)
        nv = new_ship.get(a)
        if ov != nv:
            out.append(f'player.ship.{a} {ov}→{nv}')
    return out


# ---------- emitters ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'


def _display(gs: ET.Element, loc: Locale, gid: str) -> str:
    """`@name` → locale lookup via `resolve_attr_ref`; fallback `@id`."""
    return resolve_attr_ref(gs, loc, attr='name', fallback=gid)


def _emit_added(rec, loc_new: Locale) -> list[RuleOutput]:
    gs = rec.element
    gid = rec.key
    name = _display(gs, loc_new, gid)
    classifications = _classify(gs)
    sources_label = render_sources(None, rec.sources)
    parts = ['NEW']
    text = _format(name, classifications, sources_label, parts)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': gid,
        'kind': 'added',
        'classifications': classifications,
        'gamestart_id': gid,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'source': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_removed(rec, loc_old: Locale) -> list[RuleOutput]:
    gs = rec.element
    gid = rec.key
    name = _display(gs, loc_old, gid)
    classifications = _classify(gs)
    sources_label = render_sources(rec.sources, None)
    text = _format(name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': gid,
        'kind': 'removed',
        'classifications': classifications,
        'gamestart_id': gid,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'source': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_modified(rec, loc_old: Locale, loc_new: Locale) -> list[RuleOutput]:
    gid = rec.key
    name = _display(rec.new, loc_new, gid)
    if name == gid:
        name = _display(rec.old, loc_old, gid)
    classifications = _classify(rec.new)

    changes: list[str] = []
    # Root-level attributes.
    changes.extend(_diff_root_attrs(rec.old, rec.new))
    # Nested singletons.
    changes.extend(_diff_singleton_child(rec.old, rec.new, 'cutscene'))
    changes.extend(_diff_singleton_child(rec.old, rec.new, 'player'))
    # player/ship is nested one more level down — standalone helper.
    changes.extend(_diff_player_ship(rec.old, rec.new))
    changes.extend(_diff_singleton_child(rec.old, rec.new, 'universe'))

    if not changes:
        return []

    sources_label = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': gid,
        'kind': 'modified',
        'classifications': classifications,
        'gamestart_id': gid,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'source': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]
