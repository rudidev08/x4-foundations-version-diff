"""Ships rule: emit outputs for ship macro + ware + role changes.

Three sub-sources share the `ships` tag, distinguished by `extras.subsource`:

- `macro` — file-iteration over `assets/units/size_*/macros/ship_*_macro.xml`
  (core + DLC). Ship macro files are standalone XML (NOT `<diff>`-wrapped); each
  file IS one macro. Enumerated via `file_level.diff_files`; parse errors feed a
  synthetic `_MacroReport` wrapper that rides the same `forward_incomplete_many`
  pipeline the library-diff sub-sources use.
- `ware` — `diff_library` on `libraries/wares.xml` filtered to ship wares
  (`@transport='ship' OR 'ship' in tags OR @group='drones'`). Macro resolution
  for the ware row goes through `<component ref>` → `resolve_macro_path(
  kind='ships')` so drone wares (whose macro filenames don't match the
  `ship_*_macro.xml` glob) still surface macro-derived display names.
- `role` — `diff_library` on `libraries/ships.xml`. Simple entity model —
  `<ship @id>` with category/pilot/basket/drop/people fields.

Locale page 20101 ("Ships") drives display names for the macro + ware
sub-sources; role rows use the `@id` attribute directly (no locale ref).

Follows the Wave 2 skeleton: separate `_MacroReport` for parse errors, tuple
entity keys `(subsource, inner_key)`, `forward_incomplete_many` over three
(report, label) pairs. Ware-side macro parse errors land in the SAME
`_MacroReport` with `affected_keys=[ware_id]` so the ware row (not the macro
row) gets flagged incomplete.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.change_map import ChangeKind
from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.file_level import diff_files
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.macro_diff import diff_attrs
from src.lib.paths import resolve_macro_path, source_of
from src.lib.rule_output import RuleOutput, render_sources
from src.rules._wave1_common import diff_productions


TAG = 'ships'
LOCALE_PAGE = 20101

# Ship-macro stat field spec: (xpath_from_macro_element, attr, label).
# Used for both diff (modified macros) and collect (added/removed display).
MACRO_STATS: list[tuple[str, str, str]] = [
    ('properties/hull',     'max',      'hull_max'),
    ('properties/people',   'capacity', 'people_cap'),
    ('properties/physics',  'mass',     'mass'),
    ('properties/jerk',     'forward',  'jerk_forward'),
    ('properties/jerk',     'strafe',   'jerk_strafe'),
    ('properties/jerk',     'angular',  'jerk_angular'),
    ('properties/purpose',  'primary',  'purpose_primary'),
    ('properties/storage',  'missile',  'storage_missile'),
]

# Ware-level stat spec: (xpath_or_dot, attr, label). '.' = ware root attribute.
WARE_STATS: list[tuple[str, str, str]] = [
    ('price', 'min',     'price_min'),
    ('price', 'average', 'price_avg'),
    ('price', 'max',     'price_max'),
    ('.',     'volume',  'volume'),
]

# Role-entity stat spec.
ROLE_STATS: list[tuple[str, str, str]] = [
    ('category', 'tags',    'category_tags'),
    ('category', 'faction', 'category_faction'),
    ('category', 'size',    'category_size'),
    ('basket',   'basket',  'basket'),
    ('drop',     'ref',     'drop_ref'),
    ('people',   'ref',     'people_ref'),
]

# Glob patterns for the macro sub-source (core + DLC extensions).
MACRO_GLOBS = [
    'assets/units/**/ship_*_macro.xml',
    'extensions/*/assets/units/**/ship_*_macro.xml',
]

# Classification token filter: `ship` is on every ship ware; strip so
# classifications stay meaningful per spec.
_GENERIC_FILTER = frozenset({'ship'})


# ---------- synthetic macro-report wrapper ----------


@dataclass
class _MacroReport:
    """Synthetic DiffReport-shaped wrapper for macro parse errors.

    Ship macro files are standalone (not DLC-patched), so they don't produce a
    real DiffReport — but parse errors must still ride the `forward_incomplete`
    pipeline so a malformed macro doesn't silently drop its ship's data.

    The rule keeps TWO lists:
    - `failures`           — macro-sub-source file parse errors; routed with
                              `subsource='macro'` via `forward_incomplete_many`.
    - `ware_failures`      — ware-resolved macro parse errors; routed with
                              `subsource='ware'` so the malformed macro
                              contaminates the ware row (keyed by ware_id).
    """
    failures: list[tuple[str, dict]] = field(default_factory=list)
    ware_failures: list[tuple[str, dict]] = field(default_factory=list)
    warnings: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


# ---------- wrapper to prefix affected_keys with (subsource, ...) tuples ----------


@dataclass
class _PrefixedReport:
    """Wraps a DiffReport so `forward_incomplete` matches tuple entity keys.

    The ship rule uses `(subsource, inner_key)` tuple entity_keys; underlying
    DiffReport.failures carry raw ids in `affected_keys`. This wrapper adapts
    the two without mutating the original report.

    `extra_failures` — additional failures to append in-scope (e.g., ware-
    resolved macro parse errors that should contaminate their ware row).
    """
    report: object
    subsource: str
    extra_failures: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        base = bool(getattr(self.report, 'incomplete', False))
        return base or bool(self.extra_failures)

    @property
    def failures(self) -> list[tuple[str, dict]]:
        out = []
        for text, extras in list(getattr(self.report, 'failures', []) or []) \
                            + list(self.extra_failures):
            new_extras = dict(extras)
            ak = new_extras.get('affected_keys') or []
            new_extras['affected_keys'] = [(self.subsource, k) for k in ak]
            out.append((text, new_extras))
        return out

    @property
    def warnings(self) -> list[tuple[str, dict]]:
        return list(getattr(self.report, 'warnings', []) or [])


# ---------- top-level entry ----------


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit ships rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; the rule enumerates macros via
    `file_level.diff_files` and wares/roles via `diff_library` — no dependency
    on the top-level change_map.
    """
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    macro_report = _MacroReport()

    # Macro sub-source (file iteration).
    outputs.extend(_emit_macro_subsource(
        old_root, new_root, loc_old, loc_new, macro_report,
    ))

    # Ware sub-source.
    ware_report = diff_library(
        old_root, new_root, 'libraries/wares.xml', './/ware',
        key_fn=_ware_key_fn, key_fn_identity='ships_ware',
    )
    outputs.extend(_emit_ware_subsource(
        old_root, new_root, ware_report, loc_old, loc_new, macro_report,
    ))

    # Role sub-source.
    role_report = diff_library(
        old_root, new_root, 'libraries/ships.xml', './/ship',
        key_fn=lambda e: e.get('id'), key_fn_identity='ships_role',
    )
    outputs.extend(_emit_role_subsource(role_report))

    forward_incomplete_many(
        [
            (macro_report, 'macro'),
            (_PrefixedReport(ware_report, 'ware',
                             extra_failures=list(macro_report.ware_failures)),
             'ware'),
            (_PrefixedReport(role_report, 'role'), 'role'),
        ],
        outputs, tag=TAG,
    )
    forward_warnings(ware_report.warnings, outputs, tag=TAG)
    forward_warnings(role_report.warnings, outputs, tag=TAG)
    return outputs


def _ware_key_fn(e: ET.Element) -> Optional[str]:
    """Filter for ship/drone wares. Returns ware id or None."""
    if e.get('transport') == 'ship':
        return e.get('id')
    if 'ship' in (e.get('tags') or '').split():
        return e.get('id')
    if e.get('group') == 'drones':
        return e.get('id')
    return None


# ---------- macro sub-source ----------


def _emit_macro_subsource(old_root: Path, new_root: Path,
                          loc_old: Locale, loc_new: Locale,
                          macro_report: _MacroReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rel, kind, old_bytes, new_bytes in diff_files(old_root, new_root,
                                                      MACRO_GLOBS):
        if kind == ChangeKind.ADDED:
            outputs.extend(_emit_macro_added(
                rel, new_bytes, loc_new, macro_report,
            ))
        elif kind == ChangeKind.DELETED:
            outputs.extend(_emit_macro_removed(
                rel, old_bytes, loc_old, macro_report,
            ))
        elif kind == ChangeKind.MODIFIED:
            outputs.extend(_emit_macro_modified(
                rel, old_bytes, new_bytes, loc_old, loc_new, macro_report,
            ))
    return outputs


def _parse_macro_bytes(data: bytes, rel: str,
                       macro_report: _MacroReport,
                       affected_keys: Optional[list] = None
                       ) -> Optional[ET.Element]:
    """Parse raw bytes to <macro> element, logging failures to macro_report.

    `affected_keys` defaults to `[]` (file-level failure, contaminates by
    subsource). Caller passes ware-id lists when resolving a ware's macro —
    those failures route to `macro_report.ware_failures` so the ware row
    gets contaminated (keyed by its ware_id).
    """
    if data is None:
        return None
    ak = list(affected_keys) if affected_keys else []
    # Caller-affected (ware-side) vs. file-level routing.
    dest = macro_report.ware_failures if ak else macro_report.failures
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        dest.append((
            f'ship macro parse error: {rel}',
            {'reason': 'parse_error', 'path': rel,
             'detail': str(e), 'affected_keys': ak},
        ))
        return None
    macro = root.find('macro') if root.tag == 'macros' else (
        root if root.tag == 'macro' else None
    )
    if macro is None:
        dest.append((
            f'ship macro missing root: {rel}',
            {'reason': 'missing_macro_root', 'path': rel,
             'affected_keys': ak},
        ))
        return None
    return macro


def _macro_name_from_bytes(data: Optional[bytes], rel: str) -> str:
    """Return macro @name from parsed bytes, or filename stem on any error."""
    if data is None:
        return Path(rel).stem
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return Path(rel).stem
    macro = root.find('macro') if root.tag == 'macros' else (
        root if root.tag == 'macro' else None
    )
    if macro is None:
        return Path(rel).stem
    return macro.get('name') or Path(rel).stem


def _emit_macro_added(rel: str, new_bytes: bytes, loc_new: Locale,
                      macro_report: _MacroReport) -> list[RuleOutput]:
    macro = _parse_macro_bytes(new_bytes, rel, macro_report)
    macro_name = _macro_name_from_bytes(new_bytes, rel)
    source = source_of(rel)
    if macro is None:
        name = macro_name
        classifications: list[str] = []
    else:
        name = _macro_display_name(macro, loc_new, macro_name)
        classifications = _macro_classifications(macro)
    srcs = render_sources(None, [source])
    text = _format(name, classifications, srcs, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('macro', macro_name),
        'kind': 'added',
        'subsource': 'macro',
        'classifications': classifications,
        'macro': macro_name,
        'path': rel,
        'source': source,
        'new_sources': [source],
        'sources': [source],
    })]


def _emit_macro_removed(rel: str, old_bytes: bytes, loc_old: Locale,
                        macro_report: _MacroReport) -> list[RuleOutput]:
    macro = _parse_macro_bytes(old_bytes, rel, macro_report)
    macro_name = _macro_name_from_bytes(old_bytes, rel)
    source = source_of(rel)
    if macro is None:
        name = macro_name
        classifications: list[str] = []
    else:
        name = _macro_display_name(macro, loc_old, macro_name)
        classifications = _macro_classifications(macro)
    srcs = render_sources([source], None)
    text = _format(name, classifications, srcs, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('macro', macro_name),
        'kind': 'removed',
        'subsource': 'macro',
        'classifications': classifications,
        'macro': macro_name,
        'path': rel,
        'source': source,
        'old_sources': [source],
        'sources': [source],
    })]


def _emit_macro_modified(rel: str, old_bytes: bytes, new_bytes: bytes,
                         loc_old: Locale, loc_new: Locale,
                         macro_report: _MacroReport) -> list[RuleOutput]:
    old_macro = _parse_macro_bytes(old_bytes, rel, macro_report)
    new_macro = _parse_macro_bytes(new_bytes, rel, macro_report)
    macro_name = _macro_name_from_bytes(new_bytes, rel) \
                 or _macro_name_from_bytes(old_bytes, rel)
    source = source_of(rel)

    if old_macro is None or new_macro is None:
        # Parse error already logged; emit a placeholder row so the macro still
        # appears in outputs. forward_incomplete_many will mark it incomplete
        # via the subsource scope (affected_keys=[] → global contamination).
        name = macro_name
        srcs = render_sources([source], [source])
        text = _format(name, [], srcs, ['modified (parse error)'])
        return [RuleOutput(tag=TAG, text=text, extras={
            'entity_key': ('macro', macro_name),
            'kind': 'modified',
            'subsource': 'macro',
            'classifications': [],
            'macro': macro_name,
            'path': rel,
            'source': source,
            'old_sources': [source],
            'new_sources': [source],
            'sources': [source],
        })]

    name = _macro_display_name(new_macro, loc_new, macro_name) \
           or _macro_display_name(old_macro, loc_old, macro_name)
    classifications = _macro_classifications(new_macro) \
                      or _macro_classifications(old_macro)

    changes: list[str] = []
    for label, (ov, nv) in diff_attrs(old_macro, new_macro, MACRO_STATS).items():
        changes.append(f'{label} {ov}→{nv}')
    if not changes:
        return []

    srcs = render_sources([source], [source])
    text = _format(name, classifications, srcs, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('macro', macro_name),
        'kind': 'modified',
        'subsource': 'macro',
        'classifications': classifications,
        'macro': macro_name,
        'path': rel,
        'source': source,
        'old_sources': [source],
        'new_sources': [source],
        'sources': [source],
    })]


def _macro_display_name(macro: ET.Element, loc: Locale,
                        fallback: str) -> str:
    ident = macro.find('properties/identification')
    if ident is None:
        return fallback
    return resolve_attr_ref(ident, loc, attr='name', fallback=fallback)


def _macro_classifications(macro: ET.Element) -> list[str]:
    """Return [macro@class, ship@type] minus the generic filter."""
    tokens: list[str] = []
    cls = macro.get('class')
    if cls:
        tokens.append(cls)
    ship = macro.find('properties/ship')
    if ship is not None:
        t = ship.get('type')
        if t:
            tokens.append(t)
    return [t for t in tokens if t not in _GENERIC_FILTER]


# ---------- ware sub-source ----------


def _emit_ware_subsource(old_root: Path, new_root: Path, ware_report,
                         loc_old: Locale, loc_new: Locale,
                         macro_report: _MacroReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in ware_report.added:
        outputs.extend(_emit_ware_added(new_root, rec, loc_new, macro_report))
    for rec in ware_report.removed:
        outputs.extend(_emit_ware_removed(old_root, rec, loc_old, macro_report))
    for rec in ware_report.modified:
        outputs.extend(_emit_ware_modified(
            old_root, new_root, rec, loc_old, loc_new, macro_report,
        ))
    return outputs


def _emit_ware_added(new_root: Path, rec, loc_new: Locale,
                     macro_report: _MacroReport) -> list[RuleOutput]:
    ware = rec.element
    ware_id = rec.key
    macro, _ = _resolve_ware_macro(new_root, ware, rec.ref_sources,
                                    ware_id, macro_report)
    name = _ware_display_name(ware, macro, loc_new, ware_id)
    classifications = _ware_classifications(ware)
    srcs = render_sources(None, rec.sources)
    text = _format(name, classifications, srcs, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ware', ware_id),
        'kind': 'added',
        'subsource': 'ware',
        'classifications': classifications,
        'ware_id': ware_id,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_ware_removed(old_root: Path, rec, loc_old: Locale,
                       macro_report: _MacroReport) -> list[RuleOutput]:
    ware = rec.element
    ware_id = rec.key
    macro, _ = _resolve_ware_macro(old_root, ware, rec.ref_sources,
                                    ware_id, macro_report)
    name = _ware_display_name(ware, macro, loc_old, ware_id)
    classifications = _ware_classifications(ware)
    srcs = render_sources(rec.sources, None)
    text = _format(name, classifications, srcs, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ware', ware_id),
        'kind': 'removed',
        'subsource': 'ware',
        'classifications': classifications,
        'ware_id': ware_id,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_ware_modified(old_root: Path, new_root: Path, rec,
                        loc_old: Locale, loc_new: Locale,
                        macro_report: _MacroReport) -> list[RuleOutput]:
    ware_id = rec.key
    new_macro, _ = _resolve_ware_macro(new_root, rec.new, rec.new_ref_sources,
                                        ware_id, macro_report)
    old_macro, _ = _resolve_ware_macro(old_root, rec.old, rec.old_ref_sources,
                                        ware_id, macro_report)
    name = _ware_display_name(rec.new, new_macro, loc_new, ware_id)
    if name == ware_id:
        name = _ware_display_name(rec.old, old_macro, loc_old, ware_id)
    classifications = _ware_classifications(rec.new)

    changes: list[str] = []
    changes.extend(_ware_stat_diff(rec.old, rec.new))
    changes.extend(_owner_diff(rec.old, rec.new))
    changes.extend(_licence_diff(rec.old, rec.new))
    changes.extend(_production_diff(rec.old, rec.new))

    if not changes:
        return []

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, srcs, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ware', ware_id),
        'kind': 'modified',
        'subsource': 'ware',
        'classifications': classifications,
        'ware_id': ware_id,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]


def _resolve_ware_macro(root: Path, ware: ET.Element,
                         ref_sources: dict, ware_id: str,
                         macro_report: _MacroReport
                         ) -> tuple[Optional[ET.Element], Optional[Path]]:
    """Resolve the ware's macro via <component ref> → resolve_macro_path.

    Parse errors on the resolved macro file go into macro_report with
    `affected_keys=[ware_id]` so the ware row is marked incomplete.
    """
    component = ware.find('component') if ware is not None else None
    if component is None:
        return None, None
    ref = component.get('ref')
    if not ref:
        return None, None
    owner_short = (ref_sources or {}).get('component/@ref', 'core')
    if owner_short == 'core':
        pkg_root = root
    else:
        pkg_root = root / 'extensions' / f'ego_dlc_{owner_short}'
        if not pkg_root.is_dir():
            pkg_root = root
    path = resolve_macro_path(root, pkg_root, ref, kind='ships')
    if path is None:
        return None, None
    try:
        data = path.read_bytes()
    except OSError:
        return None, path
    rel = str(path.relative_to(root)) if path.is_absolute() else str(path)
    macro = _parse_macro_bytes(data, rel, macro_report, affected_keys=[ware_id])
    return macro, path


def _ware_display_name(ware: ET.Element, macro: Optional[ET.Element],
                       loc: Locale, fallback: str) -> str:
    """Resolve name via macro's identification first, then ware @name."""
    if macro is not None:
        ident = macro.find('properties/identification')
        if ident is not None:
            val = resolve_attr_ref(ident, loc, attr='name', fallback='')
            if val:
                return val
    return resolve_attr_ref(ware, loc, attr='name', fallback=fallback)


def _ware_classifications(ware: ET.Element) -> list[str]:
    """Return [transport, ...tags, licence] minus generic filter + deprecated.

    `_GENERIC_FILTER` applies to every token source (transport attr and tag
    tokens alike) — `transport="ship"` carries no extra signal on top of the
    `tags="ship"` already filtered out, so we drop it uniformly.
    """
    tokens: list[str] = []
    transport = ware.get('transport')
    if transport:
        tokens.append(transport)
    tags = (ware.get('tags') or '').split()
    for t in sorted(set(tags)):
        if t == 'deprecated':
            continue
        tokens.append(t)
    restriction = ware.find('restriction')
    if restriction is not None:
        lic = restriction.get('licence')
        if lic:
            tokens.append(lic)
    # Apply generic filter and de-dup, preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in _GENERIC_FILTER or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _ware_stat_diff(old_ware: ET.Element, new_ware: ET.Element) -> list[str]:
    out: list[str] = []
    for xpath, attr, label in WARE_STATS:
        ov = _elem_attr_root(old_ware, xpath, attr)
        nv = _elem_attr_root(new_ware, xpath, attr)
        if ov != nv:
            out.append(f'{label} {ov}→{nv}')
    return out


def _elem_attr_root(ware: ET.Element, xpath: str, attr: str) -> Optional[str]:
    if xpath == '.':
        return ware.get(attr)
    el = ware.find(xpath)
    return None if el is None else el.get(attr)


def _owner_diff(old_ware: ET.Element, new_ware: ET.Element) -> list[str]:
    old_set = {o.get('faction') for o in old_ware.findall('owner')
               if o.get('faction')}
    new_set = {o.get('faction') for o in new_ware.findall('owner')
               if o.get('faction')}
    if old_set == new_set:
        return []
    added = new_set - old_set
    removed = old_set - new_set
    parts = []
    if added:
        parts.append('added={' + ','.join(sorted(added)) + '}')
    if removed:
        parts.append('removed={' + ','.join(sorted(removed)) + '}')
    return ['owner_factions ' + ' '.join(parts)]


def _licence_diff(old_ware: ET.Element, new_ware: ET.Element) -> list[str]:
    old_el = old_ware.find('restriction')
    new_el = new_ware.find('restriction')
    old_lic = old_el.get('licence') if old_el is not None else None
    new_lic = new_el.get('licence') if new_el is not None else None
    if old_lic == new_lic:
        return []
    return [f'licence {old_lic}→{new_lic}']


def _production_diff(old_ware: ET.Element, new_ware: ET.Element) -> list[str]:
    """Production diff for ship wares. Reuses the Wave 1 helper labels."""
    return diff_productions(old_ware, new_ware)


# ---------- role sub-source ----------


def _emit_role_subsource(role_report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in role_report.added:
        outputs.extend(_emit_role_added(rec))
    for rec in role_report.removed:
        outputs.extend(_emit_role_removed(rec))
    for rec in role_report.modified:
        outputs.extend(_emit_role_modified(rec))
    return outputs


def _emit_role_added(rec) -> list[RuleOutput]:
    role = rec.element
    role_id = rec.key
    classifications = _role_classifications(role)
    srcs = render_sources(None, rec.sources)
    text = _format(role_id, classifications, srcs, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('role', role_id),
        'kind': 'added',
        'subsource': 'role',
        'classifications': classifications,
        'role_id': role_id,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_role_removed(rec) -> list[RuleOutput]:
    role = rec.element
    role_id = rec.key
    classifications = _role_classifications(role)
    srcs = render_sources(rec.sources, None)
    text = _format(role_id, classifications, srcs, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('role', role_id),
        'kind': 'removed',
        'subsource': 'role',
        'classifications': classifications,
        'role_id': role_id,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_role_modified(rec) -> list[RuleOutput]:
    role_id = rec.key
    classifications = _role_classifications(rec.new)

    changes: list[str] = []
    for label, (ov, nv) in diff_attrs(rec.old, rec.new, ROLE_STATS).items():
        changes.append(f'{label} {ov}→{nv}')
    changes.extend(_pilot_select_diff(rec.old, rec.new))

    if not changes:
        return []

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(role_id, classifications, srcs, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('role', role_id),
        'kind': 'modified',
        'subsource': 'role',
        'classifications': classifications,
        'role_id': role_id,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]


def _role_classifications(role: ET.Element) -> list[str]:
    """Return [...tags, size] from <category> (tags is a bracket-list)."""
    out: list[str] = []
    category = role.find('category')
    if category is not None:
        tags_raw = (category.get('tags') or '').strip()
        if tags_raw:
            inner = tags_raw.strip('[]')
            for t in (tok.strip() for tok in inner.split(',')):
                if t and t not in _GENERIC_FILTER:
                    out.append(t)
        size = category.get('size')
        if size:
            out.append(size)
    return out


def _pilot_select_diff(old_role: ET.Element, new_role: ET.Element) -> list[str]:
    old_sel = old_role.find('pilot/select')
    new_sel = new_role.find('pilot/select')
    out: list[str] = []
    for attr, label in (('faction', 'pilot_faction'), ('tags', 'pilot_tags')):
        ov = old_sel.get(attr) if old_sel is not None else None
        nv = new_sel.get(attr) if new_sel is not None else None
        if ov != nv:
            out.append(f'{label} {ov}→{nv}')
    return out


# ---------- formatting ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'
