"""Missiles rule: emit outputs for missile changes.

Iterates missile wares (`group="missiles"`) in old + new `libraries/wares.xml`
(core + DLC) to detect adds / deletes / stat changes / deprecation.

For each missile:
- Display name via locale
- Missile class (e.g. `mediumguided`, `smalldumbfire`, `largetorpedo`) from the
  `<missile tags="...">` attribute on the macro
- Stat diffs: damage, range, lifetime, guided flag, reload, hull, countermeasure
  resilience, and newly-introduced attributes (e.g. `shielddisruption`)
- Deprecation: ware `tags="deprecated"` in the new version but not the old
"""
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ElementTree

from src.lib.xml_utils import load_macro
from src.lib.locale import Locale, display_name
from src.lib.macro_diff import collect_attrs, diff_attrs
from src.lib.paths import source_of
from src.lib.rule_output import RuleOutput, format_row


TAG = 'missiles'
LOCALE_PATH = 't/0001-l044.xml'


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Runs independently of change_map — iterates missile wares across both trees.

    `changes` argument kept for uniform rule interface but unused; missile changes
    aren't fully derivable from file-level deltas since ware deprecation may land
    on a line in wares.xml without any macro file diff.
    """
    locale_old = Locale(old_root / LOCALE_PATH)
    locale_new = Locale(new_root / LOCALE_PATH)

    old_wares = _load_missile_wares(old_root)
    new_wares = _load_missile_wares(new_root)

    outputs: list[RuleOutput] = []
    for missile_id in sorted(new_wares.keys() - old_wares.keys()):
        outputs.extend(_added(new_root, *new_wares[missile_id], locale_new))
    for missile_id in sorted(old_wares.keys() - new_wares.keys()):
        outputs.extend(_deleted(old_root, *old_wares[missile_id], locale_old))
    for missile_id in sorted(old_wares.keys() & new_wares.keys()):
        outputs.extend(_diff(
            old_root, new_root, missile_id,
            *old_wares[missile_id], *new_wares[missile_id],
            locale_old, locale_new,
        ))
    return outputs


def _load_missile_wares(root: Path) -> dict[str, tuple[str, ElementTree.Element]]:
    """Return {ware_id: (wares_xml_rel_path, ware_element)} for all missile wares
    across core + every DLC wares.xml found."""
    result: dict[str, tuple[str, ElementTree.Element]] = {}
    for wares_path in _wares_files(root):
        rel = str(wares_path.relative_to(root))
        tree = ElementTree.parse(wares_path).getroot()
        for ware in tree.findall('.//ware[@group="missiles"]'):
            ware_id = ware.get('id')
            if ware_id:
                result[ware_id] = (rel, ware)
    return result


def _wares_files(root: Path) -> list[Path]:
    found = list(root.glob('libraries/wares.xml'))
    found.extend(root.glob('extensions/*/libraries/wares.xml'))
    return found


def _added(new_root, wares_rel, ware, locale_new) -> list[RuleOutput]:
    ware_id = ware.get('id')
    macro_path = _macro_path(new_root, wares_rel, ware)
    macro = load_macro(macro_path) if macro_path else None
    name = display_name(macro, locale_new) if macro is not None else ware_id
    source = source_of(wares_rel)
    classifications = _missile_class(macro) if macro is not None else None
    stats = _stats(macro) if macro is not None else {}
    deprecated = 'deprecated' in (ware.get('tags') or '')

    parts = ['NEW']
    if deprecated:
        parts.append('already deprecated on release')
    if stats:
        parts.append(_stat_summary(stats))

    return [RuleOutput(
        tag='missiles',
        text=format_row(TAG, name, [classifications] if classifications else [], f"[{source}]" if source != "core" else "", parts),
        extras={
            'subsource': 'missiles',
            'entity_key': (ware_id,),
            'classifications': [classifications or 'missile', 'added'],
            'ware_id': ware_id,
            'name': name,
            'source': source,
            'class': classifications,
            'stats_new': stats,
            'kind': 'added',
        },
    )]


def _deleted(old_root, wares_rel, ware, locale_old) -> list[RuleOutput]:
    ware_id = ware.get('id')
    macro_path = _macro_path(old_root, wares_rel, ware)
    macro = load_macro(macro_path) if macro_path else None
    name = display_name(macro, locale_old) if macro is not None else ware_id
    source = source_of(wares_rel)
    classifications = _missile_class(macro) if macro is not None else None
    return [RuleOutput(
        tag='missiles',
        text=format_row(TAG, name, [classifications] if classifications else [], f"[{source}]" if source != "core" else "", ['REMOVED']),
        extras={
            'subsource': 'missiles',
            'entity_key': (ware_id,),
            'classifications': [classifications or 'missile', 'removed'],
            'ware_id': ware_id,
            'name': name,
            'source': source,
            'class': classifications,
            'kind': 'removed',
        },
    )]


def _diff(old_root, new_root, ware_id,
          old_rel, old_ware, new_rel, new_ware,
          locale_old, locale_new) -> list[RuleOutput]:
    old_macro = load_macro(_macro_path(old_root, old_rel, old_ware))
    new_macro = load_macro(_macro_path(new_root, new_rel, new_ware))
    name = (display_name(new_macro, locale_new) if new_macro is not None
            else display_name(old_macro, locale_old) if old_macro is not None
            else ware_id)
    source = source_of(new_rel)
    cls_old = _missile_class(old_macro) if old_macro is not None else None
    cls_new = _missile_class(new_macro) if new_macro is not None else None

    old_tags = old_ware.get('tags') or ''
    new_tags = new_ware.get('tags') or ''
    newly_deprecated = 'deprecated' not in old_tags and 'deprecated' in new_tags
    un_deprecated = 'deprecated' in old_tags and 'deprecated' not in new_tags

    stat_diff = _diff_stats(old_macro, new_macro) if old_macro is not None and new_macro is not None else {}
    class_change = cls_old != cls_new

    parts: list[str] = []
    if newly_deprecated:
        parts.append('DEPRECATED')
    if un_deprecated:
        parts.append('un-deprecated')
    for k, (old_value, new_value) in stat_diff.items():
        parts.append(f'{k} {old_value}→{new_value}')
    if class_change:
        parts.append(f'class {cls_old}→{cls_new}')

    if not parts:
        return []

    return [RuleOutput(
        tag='missiles',
        text=format_row(TAG, name, [cls_new or cls_old] if (cls_new or cls_old) else [], f"[{source}]" if source != "core" else "", parts),
        extras={
            'subsource': 'missiles',
            'entity_key': (ware_id,),
            'classifications': [(cls_new or cls_old or 'missile'), 'modified'],
            'ware_id': ware_id,
            'name': name,
            'source': source,
            'class_old': cls_old,
            'class_new': cls_new,
            'stat_diff': stat_diff,
            'newly_deprecated': newly_deprecated,
            'kind': 'modified',
        },
    )]


def _macro_path(root: Path, wares_rel: str, ware: ElementTree.Element) -> Optional[Path]:
    """Infer the macro file path from the ware's `<component ref="xxx_macro" />`.

    Macros live under `assets/props/WeaponSystems/missile/macros/`, either in core
    or within the same extension that provides the ware.
    """
    comp = ware.find('component')
    if comp is None or not comp.get('ref'):
        return None
    ref = comp.get('ref')
    wares_path = root / wares_rel
    # Walk up from wares.xml to find the package root (core or extension)
    # wares.xml lives at `{package}/libraries/wares.xml`
    pkg_root = wares_path.parent.parent  # .../libraries → up → package root
    candidate = pkg_root / 'assets' / 'props' / 'WeaponSystems' / 'missile' / 'macros' / f'{ref}.xml'
    if candidate.exists():
        return candidate
    # Core fallback (preserving case as found on disk)
    for variant in ('WeaponSystems', 'weaponsystems'):
        core = root / 'assets' / 'props' / variant / 'missile' / 'macros' / f'{ref}.xml'
        if core.exists():
            return core
    return None


def _missile_class(macro: ElementTree.Element) -> Optional[str]:
    missile = macro.find('properties/missile')
    if missile is None:
        return None
    tags = (missile.get('tags') or '').strip()
    return tags or None


# Attribute-location pairs: (element_xpath, attribute, label)
_STAT_FIELDS = (
    ('properties/explosiondamage', 'value',             'damage'),
    ('properties/explosiondamage', 'shielddisruption',  'shielddisruption'),
    ('properties/explosiondamage', 'hull',              'hull_dmg'),
    ('properties/missile',         'range',             'range'),
    ('properties/missile',         'lifetime',          'lifetime'),
    ('properties/missile',         'guided',            'guided'),
    ('properties/reload',          'time',              'reload'),
    ('properties/hull',            'max',               'HP'),
    ('properties/countermeasure',  'resilience',        'CMres'),
    ('properties/lock',            'time',              'locktime'),
    ('properties/lock',            'range',             'lockrange'),
)


def _stats(macro: ElementTree.Element) -> dict[str, str]:
    return collect_attrs(macro, list(_STAT_FIELDS))


def _diff_stats(old: ElementTree.Element, new: ElementTree.Element):
    return diff_attrs(old, new, list(_STAT_FIELDS))


def _stat_summary(stats: dict[str, str]) -> str:
    pieces = []
    for key in ('HP', 'damage', 'range', 'lifetime', 'reload', 'shielddisruption'):
        if key in stats:
            pieces.append(f'{key} {stats[key]}')
    if 'guided' in stats and stats['guided'] == '1':
        pieces.append('guided')
    return ', '.join(pieces)

