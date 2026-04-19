"""Turrets rule: emit outputs for turret ware + macro + bullet-macro changes.

Iterates turret wares (`group="turrets"`) across old/new libraries (core + DLC
patches), diffs ware-level stats, turret macro stats, and referenced bullet
macro stats. Bullet fan-out: one bullet macro can back multiple turret macros;
when a bullet changes we emit one row per turret that references it.

See `src/rules/turrets.md` for data model details.
"""
from pathlib import Path
import xml.etree.ElementTree as ElementTree
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.change_map import ChangeKind
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.macro_diff import diff_attrs, collect_attrs
from src.lib.paths import resolve_macro_path
from src.lib.rule_output import RuleOutput, format_row, render_sources
from src.lib.xml_utils import load_macro
from src.rules._wave1_common import owns, diff_productions


TAG = 'turrets'
LOCALE_PAGE = 20105  # "Weapons and Turrets"

# Ware-level stat fields (on <ware>).
WARE_STATS = [
    ('price', 'min',     'price_min'),
    ('price', 'average', 'price_avg'),
    ('price', 'max',     'price_max'),
    ('.',     'volume',  'volume'),
]

# Turret macro fields (under <properties>).
MACRO_STATS = [
    ('properties/bullet',               'class',  'bullet_class'),
    ('properties/rotationspeed',        'max',    'rotationspeed'),
    ('properties/rotationacceleration', 'max',    'rotationacceleration'),
    ('properties/hull',                 'max',    'hull'),
]

# Bullet macro fields (under <properties>). Same spec as weapons.
BULLET_STATS = [
    ('properties/ammunition', 'value',        'ammunition_value'),
    ('properties/bullet',     'speed',        'bullet_speed'),
    ('properties/bullet',     'lifetime',     'bullet_lifetime'),
    ('properties/bullet',     'amount',       'bullet_amount'),
    ('properties/bullet',     'barrelamount', 'bullet_barrelamount'),
    ('properties/bullet',     'timediff',     'bullet_timediff'),
    ('properties/bullet',     'reload',       'bullet_reload'),
    ('properties/bullet',     'heat',         'bullet_heat'),
]

# Generic tokens stripped from classification tag_tokens (on every turret
# connection). Mirrors the weapons set with 'turret' swapped for 'weapon'.
GENERIC_CLASSIFICATION_TOKENS = frozenset({'turret', 'component'})


def run(old_root: Path, new_root: Path,
        changes: list | None = None) -> list[RuleOutput]:
    """Entry point. `changes` carries file-level deltas (from change_map.build);
    passing None skips macro-only augmentation but still emits ware-diff rows.
    """
    outputs: list[RuleOutput] = []
    locale_old, locale_new = Locale.build_pair(old_root, new_root, outputs, tag=TAG)

    def _key_fn(e):
        if not owns(e, TAG):
            return None
        return e.get('id')

    ware_report = diff_library(
        old_root, new_root, 'libraries/wares.xml', './/ware',
        key_fn=_key_fn, key_fn_identity=f'{TAG}_ware_id',
    )

    # Build bullet reverse indices once per side so macro-diff emission can
    # fan out bullet changes to every turret that references them.
    old_bullet_index = _build_bullet_reverse_index(
        old_root, ware_report.effective_old_root)
    new_bullet_index = _build_bullet_reverse_index(
        new_root, ware_report.effective_new_root)

    for record in ware_report.added:
        outputs.extend(_emit_added(new_root, record, locale_new))
    for record in ware_report.removed:
        outputs.extend(_emit_removed(old_root, record, locale_old))
    for record in ware_report.modified:
        outputs.extend(_emit_modified(
            old_root, new_root, record, locale_old, locale_new,
            old_bullet_index, new_bullet_index,
        ))

    # Macro-only augmentation: bullet-macro file changes that fan out to
    # turrets whose ware entry itself didn't diff. Dual-state indexing: a turret
    # that either referenced the bullet in old or new gets a row.
    if changes:
        outputs.extend(_emit_macro_only_bullet(
            old_root, new_root, changes, ware_report,
            old_bullet_index, new_bullet_index,
            locale_old, locale_new,
        ))
        outputs.extend(_emit_macro_only_turret(
            old_root, new_root, changes, ware_report,
            locale_old, locale_new,
        ))

    forward_incomplete(ware_report, outputs, tag=TAG)
    forward_warnings(ware_report.warnings, outputs, tag=TAG)
    return outputs


def _emit_added(new_root: Path, record, locale_new) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    name = resolve_attr_ref(ware, locale_new, attribute='name', fallback=ware_id)
    macro_path = _resolve_macro_from_ware(new_root, ware, record.ref_sources)
    turret_macro = load_macro(macro_path) if macro_path else None
    classifications = _classify(new_root, ware, macro_path, turret_macro)

    parts: list[str] = ['NEW']
    if 'deprecated' in (ware.get('tags') or ''):
        parts.append('already deprecated on release')

    ware_stats = collect_attrs(ware, WARE_STATS)
    if ware_stats:
        stat_summary = ', '.join(f'{k}={v}' for k, v in ware_stats.items())
        parts.append(stat_summary)
    if turret_macro is not None:
        macro_stats = collect_attrs(turret_macro, MACRO_STATS)
        if macro_stats:
            parts.append(', '.join(f'{k}={v}' for k, v in macro_stats.items()))

    sources_label = render_sources(None, record.sources)
    text = format_row(TAG, name, classifications, sources_label, parts)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id, 'kind': 'added',
        'classifications': classifications,
        'new_source_files': record.source_files,
        'new_sources': record.sources,
        'ref_sources': record.ref_sources,
    })]


def _emit_removed(old_root: Path, record, locale_old) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    name = resolve_attr_ref(ware, locale_old, attribute='name', fallback=ware_id)
    macro_path = _resolve_macro_from_ware(old_root, ware, record.ref_sources)
    turret_macro = load_macro(macro_path) if macro_path else None
    classifications = _classify(old_root, ware, macro_path, turret_macro)

    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id, 'kind': 'removed',
        'classifications': classifications,
        'old_source_files': record.source_files,
        'old_sources': record.sources,
        'ref_sources': record.ref_sources,
    })]


def _emit_modified(old_root: Path, new_root: Path, record, locale_old, locale_new,
                   old_bullet_index, new_bullet_index) -> list[RuleOutput]:
    ware_id = record.key
    name = resolve_attr_ref(record.new, locale_new, attribute='name', fallback=ware_id)

    old_macro_path = _resolve_macro_from_ware(old_root, record.old, record.old_ref_sources)
    new_macro_path = _resolve_macro_from_ware(new_root, record.new, record.new_ref_sources)
    old_macro = load_macro(old_macro_path) if old_macro_path else None
    new_macro = load_macro(new_macro_path) if new_macro_path else None

    classifications = _classify(new_root, record.new, new_macro_path, new_macro)

    changes: list[str] = []

    # Ware-level stats.
    for label, (old_value, new_value) in diff_attrs(record.old, record.new, WARE_STATS).items():
        changes.append(f'{label} {old_value}→{new_value}')
    # Productions.
    changes.extend(diff_productions(record.old, record.new))
    # tags (non-deprecation text change).
    old_tags = record.old.get('tags') or ''
    new_tags = record.new.get('tags') or ''
    if 'deprecated' in new_tags and 'deprecated' not in old_tags:
        changes.insert(0, 'DEPRECATED')
    elif 'deprecated' in old_tags and 'deprecated' not in new_tags:
        changes.insert(0, 'un-deprecated')
    if old_tags != new_tags:
        # Raw tag list change when it isn't *only* the deprecation toggle.
        plain_old = set(old_tags.split()) - {'deprecated'}
        plain_new = set(new_tags.split()) - {'deprecated'}
        if plain_old != plain_new:
            changes.append(f'tags {old_tags!r}→{new_tags!r}')

    # Turret macro stats.
    if old_macro is not None and new_macro is not None:
        for label, (old_value, new_value) in diff_attrs(old_macro, new_macro, MACRO_STATS).items():
            changes.append(f'{label} {old_value}→{new_value}')

    main_outputs: list[RuleOutput] = []
    if changes:
        sources_label = render_sources(record.old_sources, record.new_sources)
        text = format_row(TAG, name, classifications, sources_label, changes)
        main_outputs.append(RuleOutput(tag=TAG, text=text, extras={
            'entity_key': ware_id, 'kind': 'modified',
            'classifications': classifications,
            'old_source_files': record.old_source_files,
            'new_source_files': record.new_source_files,
            'old_sources': record.old_sources, 'new_sources': record.new_sources,
            'ref_sources': record.new_ref_sources,
        }))

    # Bullet-macro stats per turret. Subsource='bullet'.
    bullet_outputs = _emit_bullet_diff_row(
        old_root, new_root, ware_id, name, classifications,
        record.old_sources, record.new_sources,
        old_macro, new_macro, old_macro_path, new_macro_path,
    )
    return main_outputs + bullet_outputs


def _emit_bullet_diff_row(old_root, new_root, ware_id, name, classifications,
                          old_sources, new_sources,
                          old_macro, new_macro,
                          old_macro_path, new_macro_path) -> list[RuleOutput]:
    """Load each side's bullet macro, diff, emit per-turret row. Called for
    both ware-modified turrets (this file) and macro-only fan-out path."""
    old_bullet = _load_bullet_from_turret(
        old_root, old_macro, old_macro_path) if old_macro is not None else None
    new_bullet = _load_bullet_from_turret(
        new_root, new_macro, new_macro_path) if new_macro is not None else None

    if old_bullet is None or new_bullet is None:
        return []
    bullet_changes: list[str] = []
    for label, (old_value, new_value) in diff_attrs(old_bullet, new_bullet, BULLET_STATS).items():
        bullet_changes.append(f'{label} {old_value}→{new_value}')
    if not bullet_changes:
        return []
    classifications_text = f' ({", ".join(classifications)})' if classifications else ''
    sources_label = render_sources(old_sources, new_sources)
    text = f'[{TAG}] {name}{classifications_text} {sources_label} [bullet]: {", ".join(bullet_changes)}'
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id, 'kind': 'modified',
        'subsource': 'bullet',
        'classifications': classifications,
        'old_sources': old_sources, 'new_sources': new_sources,
    })]


def _emit_macro_only_turret(old_root, new_root, changes, ware_report,
                            locale_old, locale_new) -> list[RuleOutput]:
    """Turret macro file changed but its ware didn't diff. Emit a macro-diff
    row for each such turret."""
    out: list[RuleOutput] = []
    already_emitted = {record.key for record in ware_report.modified}
    turret_macro_paths = {
        ch.path for ch in changes
        if _is_turret_macro_path(ch.path) and ch.kind == ChangeKind.MODIFIED
    }
    if not turret_macro_paths:
        return out

    # Build {macro_ref → [ware_ids]} per side. Share across calls via the
    # ware_report trees.
    old_ware_by_ref = _ware_by_macro_ref(ware_report.effective_old_root)
    new_ware_by_ref = _ware_by_macro_ref(ware_report.effective_new_root)

    for macro_rel in sorted(turret_macro_paths):
        macro_ref = Path(macro_rel).stem  # `foo_macro.xml` → `foo_macro`
        ware_ids = set(old_ware_by_ref.get(macro_ref, []))
        ware_ids |= set(new_ware_by_ref.get(macro_ref, []))
        for ware_id in sorted(ware_ids):
            if ware_id in already_emitted:
                continue
            out.extend(_emit_macro_only_row(
                old_root, new_root, ware_id,
                old_ware_by_ref, new_ware_by_ref,
                ware_report, locale_old, locale_new,
            ))
    return out


def _emit_macro_only_row(old_root, new_root, ware_id,
                         old_ware_by_ref, new_ware_by_ref,
                         ware_report, locale_old, locale_new) -> list[RuleOutput]:
    """Build a row for a ware whose turret macro changed but ware entry didn't."""
    old_ware = _find_ware(ware_report.effective_old_root, ware_id)
    new_ware = _find_ware(ware_report.effective_new_root, ware_id)
    if old_ware is None or new_ware is None:
        return []
    name = resolve_attr_ref(new_ware, locale_new, attribute='name', fallback=ware_id)
    old_macro_path = _resolve_macro_from_ware(old_root, old_ware, {})
    new_macro_path = _resolve_macro_from_ware(new_root, new_ware, {})
    old_macro = load_macro(old_macro_path) if old_macro_path else None
    new_macro = load_macro(new_macro_path) if new_macro_path else None
    classifications = _classify(new_root, new_ware, new_macro_path, new_macro)

    if old_macro is None or new_macro is None:
        return []
    changes: list[str] = []
    for label, (old_value, new_value) in diff_attrs(old_macro, new_macro, MACRO_STATS).items():
        changes.append(f'{label} {old_value}→{new_value}')
    if not changes:
        return []
    sources_label = render_sources(['core'], ['core'])  # macro-only, no ware-level provenance shift
    text = format_row(TAG, name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id, 'kind': 'modified',
        'classifications': classifications,
        'old_sources': ['core'], 'new_sources': ['core'],
    })]


def _emit_macro_only_bullet(old_root, new_root, changes, ware_report,
                            old_bullet_index, new_bullet_index,
                            locale_old, locale_new) -> list[RuleOutput]:
    """A bullet macro changed. Union old+new reverse indices; emit one row per
    impacted turret ware with subsource='bullet'. Skips wares whose ware entry
    also diffed — _emit_modified already emitted their bullet row."""
    out: list[RuleOutput] = []
    changed_bullet_paths = {
        ch.path for ch in changes
        if _is_bullet_macro_path(ch.path) and ch.kind == ChangeKind.MODIFIED
    }
    if not changed_bullet_paths:
        return out

    already_emitted = {record.key for record in ware_report.modified}
    for bullet_rel in sorted(changed_bullet_paths):
        bullet_ref = Path(bullet_rel).stem
        ware_ids: set[str] = set()
        ware_ids |= set(old_bullet_index.get(bullet_ref, []))
        ware_ids |= set(new_bullet_index.get(bullet_ref, []))
        for ware_id in sorted(ware_ids):
            if ware_id in already_emitted:
                continue
            out.extend(_emit_bullet_fanout_row(
                old_root, new_root, ware_id, ware_report, locale_old, locale_new,
            ))
    return out


def _emit_bullet_fanout_row(old_root, new_root, ware_id, ware_report,
                            locale_old, locale_new) -> list[RuleOutput]:
    old_ware = _find_ware(ware_report.effective_old_root, ware_id)
    new_ware = _find_ware(ware_report.effective_new_root, ware_id)
    if old_ware is None or new_ware is None:
        return []
    name = resolve_attr_ref(new_ware, locale_new, attribute='name', fallback=ware_id)
    old_macro_path = _resolve_macro_from_ware(old_root, old_ware, {})
    new_macro_path = _resolve_macro_from_ware(new_root, new_ware, {})
    old_macro = load_macro(old_macro_path) if old_macro_path else None
    new_macro = load_macro(new_macro_path) if new_macro_path else None
    if old_macro is None or new_macro is None:
        return []
    classifications = _classify(new_root, new_ware, new_macro_path, new_macro)
    return _emit_bullet_diff_row(
        old_root, new_root, ware_id, name, classifications,
        ['core'], ['core'],  # fallback provenance for macro-only path
        old_macro, new_macro, old_macro_path, new_macro_path,
    )


def _resolve_macro_from_ware(root: Path, ware: ElementTree.Element,
                              ref_sources: dict) -> Optional[Path]:
    """Locate a turret macro from its ware's `<component ref=>`. Uses
    ref_sources to pick the DLC owning the component attribute (falls back to
    core package for macro-only paths where ref_sources is empty).
    """
    component = ware.find('component')
    if component is None:
        return None
    ref = component.get('ref')
    if not ref:
        return None
    owner_short = ref_sources.get('component/@ref', 'core') if ref_sources else 'core'
    if owner_short == 'core':
        pkg_root = root
    else:
        pkg_root = root / 'extensions' / f'ego_dlc_{owner_short}'
        if not pkg_root.is_dir():
            # Attribution points to a DLC not present on disk — fall back to
            # the root so resolve_macro_path at least finds core candidates.
            pkg_root = root
    return resolve_macro_path(root, pkg_root, ref, kind='turrets')


def _load_bullet_from_turret(root: Path, turret_macro: ElementTree.Element,
                              turret_path: Path) -> Optional[ElementTree.Element]:
    bullet_el = turret_macro.find('properties/bullet')
    if bullet_el is None:
        return None
    ref = bullet_el.get('class')
    if not ref:
        return None
    # Walk up from the turret macro's path to the package root to seed lookup.
    pkg_root = _pkg_root_of(root, turret_path)
    bullet_path = resolve_macro_path(root, pkg_root, ref, kind='bullet')
    if bullet_path is None:
        return None
    return load_macro(bullet_path)


def _pkg_root_of(root: Path, macro_path: Path) -> Path:
    """Walk up from a macro file to the core or extension package root."""
    try:
        rel = macro_path.resolve().relative_to(root.resolve())
    except ValueError:
        return root
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == 'extensions':
        return root / 'extensions' / parts[1]
    return root


def _classify(root: Path, ware: ElementTree.Element, macro_path: Optional[Path],
              turret_macro: Optional[ElementTree.Element]) -> list[str]:
    """Returns `[subtype, ...tag_tokens, maybe 'guided']`."""
    classifications: list[str] = []

    subtype = _subtype_from_macro_path(root, macro_path) if macro_path else None
    if subtype:
        classifications.append(subtype)

    # tag_tokens from the component file's connection tags.
    if macro_path is not None and turret_macro is not None:
        component_ref = _component_ref(turret_macro)
        if component_ref:
            component_path = _locate_component(root, macro_path, component_ref)
            if component_path is not None:
                tokens = _tag_tokens_from_component(component_path)
                classifications.extend(tokens)

    # Guided classification.
    if _is_guided_turret(turret_macro, ware):
        if 'guided' not in classifications:
            classifications.append('guided')

    return classifications


def _subtype_from_macro_path(root: Path, macro_path: Path) -> Optional[str]:
    """Extract the WeaponSystems subdir as the subtype token.

    Macro path shape: `{package}/assets/props/WeaponSystems/{subtype}/macros/<ref>.xml`.
    """
    try:
        rel = macro_path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    parts = [p.lower() for p in rel.parts]
    for i, p in enumerate(parts):
        if p == 'weaponsystems' and i + 1 < len(parts):
            cand = parts[i + 1]
            # Next segment after WeaponSystems is the subtype dir.
            if cand != 'macros':
                return cand
    return None


def _component_ref(turret_macro: ElementTree.Element) -> Optional[str]:
    comp = turret_macro.find('component')
    if comp is None:
        return None
    return comp.get('ref')


def _locate_component(root: Path, macro_path: Path,
                      component_ref: str) -> Optional[Path]:
    """Component file sits next to the macros/ directory: `../<ref>.xml`."""
    co_located = macro_path.parent.parent / f'{component_ref}.xml'
    if co_located.exists():
        return co_located
    # Search for a matching component across both casing variants of
    # `assets/props/WeaponSystems/` in core and every extension.
    for variant in ('WeaponSystems', 'weaponsystems'):
        for pkg_root in _iter_pkg_roots(root):
            for sub in (pkg_root / 'assets' / 'props' / variant).glob('*'):
                cand = sub / f'{component_ref}.xml'
                if cand.exists():
                    return cand
    return None


def _iter_pkg_roots(root: Path):
    yield root
    ext_dir = root / 'extensions'
    if ext_dir.is_dir():
        for e in sorted(ext_dir.iterdir()):
            if e.is_dir():
                yield e


def _tag_tokens_from_component(component_path: Path) -> list[str]:
    """Harvest tokens from the first turret-bearing connection's `tags` attribute.

    Generic tokens (GENERIC_CLASSIFICATION_TOKENS) are filtered out; the
    remainder are returned in declaration order.
    """
    try:
        doc = ElementTree.parse(component_path).getroot()
    except (FileNotFoundError, ElementTree.ParseError):
        return []
    for conn in doc.iter('connection'):
        tags = (conn.get('tags') or '').split()
        if 'turret' not in tags:
            continue
        return [t for t in tags if t and t not in GENERIC_CLASSIFICATION_TOKENS]
    return []


def _is_guided_turret(turret_macro: Optional[ElementTree.Element],
                      ware: ElementTree.Element) -> bool:
    """Guided when the ware or turret macro signals missilelauncher lineage.

    Matches:
    - Turret macro's `<bullet @class>` starts with `bullet_` and contains
      `missilelauncher` (canonical glob `bullet_*missilelauncher*`).
    - Any element under the turret macro has a `tags` attribute containing
      `missilelauncher`.
    - The ware's own `tags` contain `missilelauncher` (as observed on real
      9.00B6 `turret_*_guided_*` wares).
    """
    if ware is not None:
        tags = (ware.get('tags') or '').split()
        if 'missilelauncher' in tags:
            return True
    if turret_macro is None:
        return False
    bullet_el = turret_macro.find('properties/bullet')
    if bullet_el is not None:
        ref = bullet_el.get('class') or ''
        if ref.startswith('bullet_') and 'missilelauncher' in ref:
            return True
    for element in turret_macro.iter():
        tag_attr = element.get('tags') or ''
        if 'missilelauncher' in tag_attr.split():
            return True
    return False


def _build_bullet_reverse_index(root: Path,
                                 effective_tree: Optional[ElementTree.Element]
                                 ) -> dict[str, list[str]]:
    """Return `{bullet_macro_ref: [turret_ware_id, ...]}`.

    For each turret-owned ware, resolve its turret macro, read `<bullet
    class>`, and record the ware under that bullet ref. Dual-state: callers
    build one index per side and union when looking up.
    """
    out: dict[str, list[str]] = {}
    if effective_tree is None:
        return out
    for ware in effective_tree.iter('ware'):
        if not owns(ware, TAG):
            continue
        ware_id = ware.get('id')
        if not ware_id:
            continue
        macro_path = _resolve_macro_from_ware(root, ware, {})
        if macro_path is None:
            continue
        macro = load_macro(macro_path)
        if macro is None:
            continue
        bullet_el = macro.find('properties/bullet')
        if bullet_el is None:
            continue
        bullet_ref = bullet_el.get('class')
        if not bullet_ref:
            continue
        out.setdefault(bullet_ref, []).append(ware_id)
    return out


def _ware_by_macro_ref(effective_tree: Optional[ElementTree.Element]
                       ) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if effective_tree is None:
        return out
    for ware in effective_tree.iter('ware'):
        if not owns(ware, TAG):
            continue
        component = ware.find('component')
        if component is None:
            continue
        ref = component.get('ref')
        if not ref:
            continue
        out.setdefault(ref, []).append(ware.get('id') or '')
    return out


def _find_ware(effective_tree: Optional[ElementTree.Element],
               ware_id: str) -> Optional[ElementTree.Element]:
    if effective_tree is None:
        return None
    for ware in effective_tree.iter('ware'):
        if ware.get('id') == ware_id:
            return ware
    return None


_TURRET_MACRO_MARKERS = ('weaponsystems/',)


def _is_turret_macro_path(path: str) -> bool:
    low = path.lower()
    if '/macros/' not in low or '/turret_' not in low.replace('\\', '/'):
        return False
    # Must not be a bullet macro — those live under weaponfx/.
    if 'weaponfx' in low:
        return False
    return any(m in low for m in _TURRET_MACRO_MARKERS) and low.endswith('.xml')


def _is_bullet_macro_path(path: str) -> bool:
    low = path.lower()
    return 'weaponfx/macros/' in low and low.endswith('.xml')
