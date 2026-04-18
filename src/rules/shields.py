"""Shields rule: emit outputs for shield macro changes.

Scans core (`assets/props/SurfaceElements/`) and DLC
(`extensions/*/assets/props/surfaceelements/`, case-insensitive) shield macros.

For each changed macro:
- Resolves the display name via locale
- Classifies slot type via the referenced component file's connection tags
  (standard / advanced / *_racer / ship-specific / faction-restricted)
- Tags the output with source (`core` or DLC short name)
- Skips video macros and tutorial shields (not player-relevant)
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

from src.change_map import ChangeKind, FileChange
from src.lib import xml_utils
from src.lib.locale import Locale, display_name
from src.lib.paths import source_of


LOCALE_PATH = 't/0001-l044.xml'

_SHIELD_MACRO_RE = re.compile(
    r'(?:^|/)surfaceelements/macros/shield_[^/]+_macro\.xml$',
    re.IGNORECASE,
)

_CORE_COMPONENT_DIR = Path('assets/props/SurfaceElements')

# Tokens that appear in every shield connection — ignored when classifying slot.
_GENERIC_TOKENS = frozenset({
    'component', 'shield',
    'small', 'medium', 'large', 'extralarge',
    'hittable', 'unhittable', 'mandatory',
})

# Player-equippable slot tiers, in priority order.
_PLAYER_SLOTS = ('standard', 'advanced')

# Faction names that appear as slot-restriction tags.
_FACTION_TOKENS = frozenset({'khaak', 'xenon'})


@dataclass
class RuleOutput:
    tag: str
    text: str
    extras: dict = field(default_factory=dict)


def run(old_root: Path, new_root: Path, changes: list[FileChange]) -> list[RuleOutput]:
    loc_old = Locale(old_root / LOCALE_PATH)
    loc_new = Locale(new_root / LOCALE_PATH)
    outputs: list[RuleOutput] = []
    for ch in changes:
        if not _is_relevant_macro(ch.path):
            continue
        if ch.kind == ChangeKind.MODIFIED:
            outputs.extend(_diff(old_root, new_root, ch.path, loc_old, loc_new))
        elif ch.kind == ChangeKind.ADDED:
            outputs.extend(_added(new_root, ch.path, loc_new))
        elif ch.kind == ChangeKind.DELETED:
            outputs.extend(_deleted(old_root, ch.path, loc_old))
    return outputs


def _is_relevant_macro(path: str) -> bool:
    if not _SHIELD_MACRO_RE.search(path):
        return False
    lower = path.lower()
    if lower.endswith('_video_macro.xml'):
        return False
    if 'tutorial' in lower:
        return False
    return True


def _diff(old_root, new_root, rel, loc_old, loc_new) -> list[RuleOutput]:
    old = xml_utils.load(old_root / rel).find('macro')
    new = xml_utils.load(new_root / rel).find('macro')
    name = display_name(new, loc_new) or display_name(old, loc_old)
    source = source_of(rel)
    old_type = _slot_type(old_root, rel, old)
    new_type = _slot_type(new_root, rel, new)

    changes: list[str] = []
    old_r = old.find('properties/recharge')
    new_r = new.find('properties/recharge')
    for attr, label in (('max', 'HP'), ('rate', 'rate'), ('delay', 'delay')):
        ov = old_r.get(attr) if old_r is not None else None
        nv = new_r.get(attr) if new_r is not None else None
        if ov != nv:
            changes.append(f'{label} {ov}→{nv}')
    old_h = old.find('properties/hull')
    new_h = new.find('properties/hull')
    ov = old_h.get('max') if old_h is not None else None
    nv = new_h.get('max') if new_h is not None else None
    if ov != nv:
        changes.append(f'hull {ov}→{nv}')

    type_change = old_type != new_type
    if not changes and not type_change:
        return []

    parts = list(changes)
    if type_change:
        parts.append(f'type {old_type}→{new_type}')
    return [RuleOutput(
        tag='shields',
        text=_format(name, new_type, source, parts),
        extras={
            'macro': old.get('name'),
            'name': name,
            'source': source,
            'type_old': old_type,
            'type_new': new_type,
            'changes': changes,
        },
    )]


def _added(new_root, rel, loc_new) -> list[RuleOutput]:
    new = xml_utils.load(new_root / rel).find('macro')
    name = display_name(new, loc_new)
    source = source_of(rel)
    new_type = _slot_type(new_root, rel, new)
    r = new.find('properties/recharge')
    stats = (
        f'HP {r.get("max")}, rate {r.get("rate")}, delay {r.get("delay")}s'
        if r is not None else 'no recharge data'
    )
    return [RuleOutput(
        tag='shields',
        text=_format(name, new_type, source, [f'NEW, {stats}']),
        extras={'macro': new.get('name'), 'name': name, 'source': source, 'type_new': new_type},
    )]


def _deleted(old_root, rel, loc_old) -> list[RuleOutput]:
    old = xml_utils.load(old_root / rel).find('macro')
    name = display_name(old, loc_old)
    source = source_of(rel)
    old_type = _slot_type(old_root, rel, old)
    return [RuleOutput(
        tag='shields',
        text=_format(name, old_type, source, ['REMOVED']),
        extras={'macro': old.get('name'), 'name': name, 'source': source, 'type_old': old_type},
    )]


def _format(name: str, slot_type: Optional[str], source: str, parts: list[str]) -> str:
    type_label = f' ({slot_type})' if slot_type else ''
    source_label = f' [{source}]' if source != 'core' else ''
    return f'[shields] {name}{type_label}{source_label}: {", ".join(parts)}'


def _slot_type(root: Path, macro_rel: str, macro: ET.Element) -> Optional[str]:
    comp = macro.find('component')
    if comp is None or not comp.get('ref'):
        return None
    path = _component_path(root, macro_rel, comp.get('ref'))
    if path is None:
        return None
    for conn in xml_utils.load(path).iter('connection'):
        tags = conn.get('tags', '')
        if 'shield' not in tags or 'deprecated' in tags:
            continue
        tokens = [t for t in tags.split() if t and t not in _GENERIC_TOKENS]
        # Player-equippable tiers
        for preferred in _PLAYER_SLOTS:
            if preferred in tokens:
                return preferred
        # Racer / custom variants ending in _racer
        for t in tokens:
            if t.endswith('_racer'):
                return t
        # Ship-specific (locked to one ship class)
        for t in tokens:
            if t.startswith('ship_') or '_mothership_' in t or '_battleship_' in t:
                return t
        # Faction-restricted (NPC)
        for t in tokens:
            if t in _FACTION_TOKENS:
                return t
        # Fallback: first non-generic token, if any
        if tokens:
            return tokens[0]
    return None


def _component_path(root: Path, macro_rel: str, ref: str) -> Optional[Path]:
    co_located = (root / macro_rel).parent.parent / f'{ref}.xml'
    if co_located.exists():
        return co_located
    core_path = root / _CORE_COMPONENT_DIR / f'{ref}.xml'
    if core_path.exists():
        return core_path
    return None
