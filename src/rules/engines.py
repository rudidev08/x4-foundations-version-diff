"""Engines rule: emit outputs for engine ware + macro changes.

Ware-driven — iterates `group="engines"` wares in core + DLC `libraries/wares.xml`
via `diff_library`. For each changed ware:
- Display name via locale page 20107
- Classifications [race, size, type, mk] parsed from ware id
- Ware stat diff (price min/avg/max, volume)
- Production diff (keyed by @method)
- Macro stat diff (boost, travel, thrust, hull) via `resolve_macro_path`
- Deprecation toggle (tags transition)

Uses `_wave1_common.owns(ware, 'engines')` for disjoint ownership and
`diff_productions` for pinned production-label forms. Provenance via
`EntityRecord.sources` / `ModifiedRecord.{old,new}_sources`.
"""
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.macro_diff import diff_attrs, diff_labels
from src.lib.paths import resolve_macro_path
from src.lib.rule_output import RuleOutput, format_row, render_sources
from src.lib.xml_utils import load_macro
from src.rules._wave1_common import owns, diff_productions


TAG = 'engines'
LOCALE_PAGE = 20107

# (xpath_or_dot, attribute, label) — '.' means the attribute lives on the ware root.
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

# Engine id regex: engine_<race>_<size>_<type>_<sequence>_<mk>
# e.g. engine_arg_m_combat_01_mk1 → (arg, m, combat, mk1)
_ENGINE_ID_RE = re.compile(r'^engine_([a-z]+)_([a-z])_([a-z]+)_\d+_([a-z0-9]+)$')

def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit engine rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused (ware-driven).
    """
    outputs: list[RuleOutput] = []
    locale_old, locale_new = Locale.build_pair(old_root, new_root, outputs, tag=TAG)

    def _key_fn(e: ElementTree.Element):
        return e.get('id') if owns(e, TAG) else None

    report = diff_library(
        old_root, new_root, 'libraries/wares.xml', './/ware',
        key_fn=_key_fn, key_fn_identity=f'{TAG}_ware_id',
    )
    for record in report.added:
        outputs.extend(_emit_added(new_root, record, locale_new))
    for record in report.removed:
        outputs.extend(_emit_removed(old_root, record, locale_old))
    for record in report.modified:
        outputs.extend(_emit_modified(old_root, new_root, record, locale_old, locale_new))

    forward_incomplete(report, outputs, tag=TAG)
    forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


def _classify(ware_id: str) -> list[str]:
    """Return [race, size, type, mk] for a valid engine id, else []."""
    m = _ENGINE_ID_RE.match(ware_id or '')
    if not m:
        return []
    tokens = [m.group(1), m.group(2), m.group(3), m.group(4)]
    return [t for t in tokens if t]


# ---------- macro path resolution ----------


def _resolve_macro(root: Path, record, side: str,
                   ref_path: str = 'component/@ref') -> Optional[Path]:
    """Resolve the macro XML path for this ware, using ref-source attribution.

    side='old' reads record.old + record.old_ref_sources.
    side='new' reads record.new + record.new_ref_sources  (ModifiedRecord).
    For EntityRecord (added/removed), use side='entity' → record.element + record.ref_sources.
    """
    if side == 'old':
        component = record.old.find('component')
        ref_sources = getattr(record, 'old_ref_sources', None) or {}
    elif side == 'new':
        component = record.new.find('component')
        ref_sources = getattr(record, 'new_ref_sources', None) or {}
    else:  # entity (added/removed EntityRecord)
        component = record.element.find('component')
        ref_sources = getattr(record, 'ref_sources', None) or {}

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


def _emit_added(new_root: Path, record, locale_new: Locale) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    name = resolve_attr_ref(ware, locale_new, attribute='name', fallback=ware_id)
    classifications = _classify(ware_id)
    macro_path = _resolve_macro(new_root, record, 'entity')
    macro_name = None
    if macro_path is not None:
        m = load_macro(macro_path)
        if m is not None:
            macro_name = m.get('name')
    sources_label = render_sources(None, record.sources)
    text = format_row(TAG, name, classifications, sources_label, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'macro': macro_name,
        'kind': 'added',
        'classifications': classifications,
        'source': record.sources,
        'sources': record.sources,
        'new_sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_removed(old_root: Path, record, locale_old: Locale) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    name = resolve_attr_ref(ware, locale_old, attribute='name', fallback=ware_id)
    classifications = _classify(ware_id)
    macro_path = _resolve_macro(old_root, record, 'entity')
    macro_name = None
    if macro_path is not None:
        m = load_macro(macro_path)
        if m is not None:
            macro_name = m.get('name')
    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'macro': macro_name,
        'kind': 'removed',
        'classifications': classifications,
        'source': record.sources,
        'sources': record.sources,
        'old_sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_modified(old_root: Path, new_root: Path, record,
                   locale_old: Locale, locale_new: Locale) -> list[RuleOutput]:
    ware_id = record.key
    name = resolve_attr_ref(record.new, locale_new, attribute='name', fallback=ware_id)
    if name == ware_id:
        # New-side missing the locale; try old-side fallback.
        name = resolve_attr_ref(record.old, locale_old, attribute='name', fallback=ware_id)
    classifications = _classify(ware_id)

    changes: list[str] = []
    changes.extend(diff_labels(record.old, record.new, WARE_STATS))
    changes.extend(diff_productions(record.old, record.new))

    # Macro stat diffs
    old_macro_path = _resolve_macro(old_root, record, 'old')
    new_macro_path = _resolve_macro(new_root, record, 'new')
    old_macro = load_macro(old_macro_path)
    new_macro = load_macro(new_macro_path)
    macro_name = None
    if new_macro is not None:
        macro_name = new_macro.get('name')
    elif old_macro is not None:
        macro_name = old_macro.get('name')
    if old_macro is not None and new_macro is not None:
        changes.extend(diff_labels(old_macro, new_macro, MACRO_STATS))

    # Lifecycle (deprecation toggle) — prepended so it reads first.
    old_tags = record.old.get('tags') or ''
    new_tags = record.new.get('tags') or ''
    if 'deprecated' in new_tags.split() and 'deprecated' not in old_tags.split():
        changes.insert(0, 'DEPRECATED')
    elif 'deprecated' in old_tags.split() and 'deprecated' not in new_tags.split():
        changes.insert(0, 'un-deprecated')

    if not changes:
        return []

    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'macro': macro_name,
        'kind': 'modified',
        'classifications': classifications,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })]
