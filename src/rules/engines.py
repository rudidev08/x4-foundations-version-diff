"""Engines rule: emit outputs for engine ware + macro changes.

Ware-driven — iterates `group="engines"` wares in core + DLC `libraries/wares.xml`
via `diff_library`. For each changed ware:
- Display name via locale page 20107
- Classifications [race, size, type, mk] parsed from ware id
- Ware stat diff (price min/avg/max, volume)
- Production diff (keyed by @method)
- Macro stat diff (boost, travel, thrust, hull) via `resolve_macro_path`
- Deprecation toggle (tags transition)

Follows the Wave 1 shared helpers: `owns(ware, 'engines')` for disjoint ownership,
`diff_productions` for pinned production-label forms. Provenance via
`EntityRecord.sources` / `ModifiedRecord.{old,new}_sources`.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.macro_diff import diff_attrs
from src.lib.paths import resolve_macro_path
from src.lib.rule_output import RuleOutput, render_sources
from src.rules._wave1_common import owns, diff_productions


TAG = 'engines'
LOCALE_PAGE = 20107

# (xpath_or_dot, attr, label) — '.' means the attribute lives on the ware root.
WARE_STATS = [
    ('price', 'min', 'price_min'),
    ('price', 'average', 'price_avg'),
    ('price', 'max', 'price_max'),
    ('.', 'volume', 'volume'),
]

MACRO_STATS = [
    ('properties/boost', 'thrust', 'boost_thrust'),
    ('properties/boost', 'acceleration', 'boost_accel'),
    ('properties/travel', 'thrust', 'travel_thrust'),
    ('properties/travel', 'attack', 'travel_attack'),
    ('properties/thrust', 'forward', 'thrust_forward'),
    ('properties/thrust', 'reverse', 'thrust_reverse'),
    ('properties/hull', 'max', 'hull_max'),
]

# Engine id regex: engine_<race>_<size>_<type>_<seq>_<mk>
# e.g. engine_arg_m_combat_01_mk1 → (arg, m, combat, mk1)
_ENGINE_ID_RE = re.compile(r'^engine_([a-z]+)_([a-z])_([a-z]+)_\d+_([a-z0-9]+)$')

# All four classification tokens are meaningful — nothing filtered.
_GENERIC_FILTER = frozenset()


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit engine rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused (ware-driven).
    """
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    def _key_fn(e: ET.Element):
        return e.get('id') if owns(e, TAG) else None

    report = diff_library(
        old_root, new_root, 'libraries/wares.xml', './/ware',
        key_fn=_key_fn, key_fn_identity=f'{TAG}_ware_id',
    )
    for rec in report.added:
        outputs.extend(_emit_added(new_root, rec, loc_new))
    for rec in report.removed:
        outputs.extend(_emit_removed(old_root, rec, loc_old))
    for rec in report.modified:
        outputs.extend(_emit_modified(old_root, new_root, rec, loc_old, loc_new))

    forward_incomplete(report, outputs, tag=TAG)
    forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


# ---------- classification ----------


def _classify(ware_id: str) -> list[str]:
    """Return [race, size, type, mk] for a valid engine id, else []."""
    m = _ENGINE_ID_RE.match(ware_id or '')
    if not m:
        return []
    tokens = [m.group(1), m.group(2), m.group(3), m.group(4)]
    return [t for t in tokens if t not in _GENERIC_FILTER]


# ---------- ware/macro stat diff ----------


def _elem_attr_root(ware: ET.Element, xpath: str, attr: str) -> Optional[str]:
    """WARE_STATS uses '.' for ware-root attributes; otherwise find via xpath."""
    if xpath == '.':
        return ware.get(attr)
    el = ware.find(xpath)
    return None if el is None else el.get(attr)


def _ware_stat_diff(old_ware: ET.Element, new_ware: ET.Element) -> list[str]:
    """Return [f'{label} {ov}→{nv}', ...] for ware attributes that changed.

    Custom walk because WARE_STATS mixes xpath-child attrs and root-attrs ('.').
    """
    out: list[str] = []
    for xpath, attr, label in WARE_STATS:
        ov = _elem_attr_root(old_ware, xpath, attr)
        nv = _elem_attr_root(new_ware, xpath, attr)
        if ov != nv:
            out.append(f'{label} {ov}→{nv}')
    return out


def _macro_stat_diff(old_macro: ET.Element, new_macro: ET.Element) -> list[str]:
    out: list[str] = []
    for label, (ov, nv) in diff_attrs(old_macro, new_macro, MACRO_STATS).items():
        out.append(f'{label} {ov}→{nv}')
    return out


# ---------- macro path resolution ----------


def _resolve_macro(root: Path, rec, side: str,
                   ref_path: str = 'component/@ref') -> Optional[Path]:
    """Resolve the macro XML path for this ware, using ref-source attribution.

    side='old' reads rec.old + rec.old_ref_sources.
    side='new' reads rec.new + rec.new_ref_sources  (ModifiedRecord).
    For EntityRecord (added/removed), use side='entity' → rec.element + rec.ref_sources.
    """
    if side == 'old':
        component = rec.old.find('component')
        ref_sources = getattr(rec, 'old_ref_sources', None) or {}
    elif side == 'new':
        component = rec.new.find('component')
        ref_sources = getattr(rec, 'new_ref_sources', None) or {}
    else:  # entity (added/removed EntityRecord)
        component = rec.element.find('component')
        ref_sources = getattr(rec, 'ref_sources', None) or {}

    if component is None:
        return None
    ref = component.get('ref')
    if not ref:
        return None

    owner_short = ref_sources.get(ref_path, 'core')
    if owner_short == 'core':
        pkg_root = root
    else:
        pkg_root = root / 'extensions' / f'ego_dlc_{owner_short}'
        if not pkg_root.is_dir():
            # Attribution points at a missing extension — skip rather than let
            # a same-named core macro stand in.
            return None
    return resolve_macro_path(root, pkg_root, ref, kind='engines')


def _load_macro(path: Optional[Path]) -> Optional[ET.Element]:
    if path is None:
        return None
    try:
        return ET.parse(path).getroot().find('macro')
    except (FileNotFoundError, ET.ParseError):
        return None


# ---------- emitters ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    return f'[{TAG}] {name}{cls} {sources_label}: {", ".join(parts)}'


def _emit_added(new_root: Path, rec, loc_new: Locale) -> list[RuleOutput]:
    ware = rec.element
    ware_id = rec.key
    name = resolve_attr_ref(ware, loc_new, attr='name', fallback=ware_id)
    classifications = _classify(ware_id)
    macro_path = _resolve_macro(new_root, rec, 'entity')
    macro_name = None
    if macro_path is not None:
        m = _load_macro(macro_path)
        if m is not None:
            macro_name = m.get('name')
    sources_label = render_sources(None, rec.sources)
    text = _format(name, classifications, sources_label, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'macro': macro_name,
        'kind': 'added',
        'classifications': classifications,
        'source': rec.sources,
        'sources': rec.sources,
        'new_sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_removed(old_root: Path, rec, loc_old: Locale) -> list[RuleOutput]:
    ware = rec.element
    ware_id = rec.key
    name = resolve_attr_ref(ware, loc_old, attr='name', fallback=ware_id)
    classifications = _classify(ware_id)
    macro_path = _resolve_macro(old_root, rec, 'entity')
    macro_name = None
    if macro_path is not None:
        m = _load_macro(macro_path)
        if m is not None:
            macro_name = m.get('name')
    sources_label = render_sources(rec.sources, None)
    text = _format(name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'macro': macro_name,
        'kind': 'removed',
        'classifications': classifications,
        'source': rec.sources,
        'sources': rec.sources,
        'old_sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_modified(old_root: Path, new_root: Path, rec,
                   loc_old: Locale, loc_new: Locale) -> list[RuleOutput]:
    ware_id = rec.key
    name = resolve_attr_ref(rec.new, loc_new, attr='name', fallback=ware_id)
    if name == ware_id:
        # New-side missing the locale; try old-side fallback.
        name = resolve_attr_ref(rec.old, loc_old, attr='name', fallback=ware_id)
    classifications = _classify(ware_id)

    changes: list[str] = []
    changes.extend(_ware_stat_diff(rec.old, rec.new))
    changes.extend(diff_productions(rec.old, rec.new))

    # Macro stat diffs
    old_macro_path = _resolve_macro(old_root, rec, 'old')
    new_macro_path = _resolve_macro(new_root, rec, 'new')
    old_macro = _load_macro(old_macro_path)
    new_macro = _load_macro(new_macro_path)
    macro_name = None
    if new_macro is not None:
        macro_name = new_macro.get('name')
    elif old_macro is not None:
        macro_name = old_macro.get('name')
    if old_macro is not None and new_macro is not None:
        changes.extend(_macro_stat_diff(old_macro, new_macro))

    # Lifecycle (deprecation toggle) — prepended so it reads first.
    old_tags = rec.old.get('tags') or ''
    new_tags = rec.new.get('tags') or ''
    if 'deprecated' in new_tags.split() and 'deprecated' not in old_tags.split():
        changes.insert(0, 'DEPRECATED')
    elif 'deprecated' in old_tags.split() and 'deprecated' not in new_tags.split():
        changes.insert(0, 'un-deprecated')

    if not changes:
        return []

    sources_label = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'macro': macro_name,
        'kind': 'modified',
        'classifications': classifications,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]
