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

Uses a separate `_MacroReport` for parse errors, with a tuple
entity keys `(subsource, inner_key)`, `forward_incomplete_many` over three
(report, label) pairs. Ware-side macro parse errors land in the SAME
`_MacroReport` with `affected_keys=[ware_id]` so the ware row (not the macro
row) gets flagged incomplete.
"""
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.change_map import ChangeKind
from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.file_level import diff_files
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.macro_diff import diff_attrs, diff_labels
from src.lib.paths import resolve_macro_path, source_of
from src.lib.rule_output import RuleOutput, format_row, render_sources
from src.rules._wave1_common import diff_productions


TAG = 'ships'
LOCALE_PAGE = 20101

# Ship-macro stat field spec: (xpath_from_macro_element, attribute, label).
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

# Ware-level stat spec: (xpath_or_dot, attribute, label). '.' = ware root attribute.
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

_GENERIC_FILTER = frozenset({'ship'})


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


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit ships rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; the rule enumerates macros via
    `file_level.diff_files` and wares/roles via `diff_library` — no dependency
    on the top-level change_map.
    """
    outputs: list[RuleOutput] = []
    locale_old, locale_new = Locale.build_pair(old_root, new_root, outputs, tag=TAG)

    macro_report = _MacroReport()

    # Macro sub-source (file iteration).
    outputs.extend(_emit_macro_subsource(
        old_root, new_root, locale_old, locale_new, macro_report,
    ))

    # Ware sub-source.
    ware_report = diff_library(
        old_root, new_root, 'libraries/wares.xml', './/ware',
        key_fn=_ware_key_fn, key_fn_identity='ships_ware',
    )
    outputs.extend(_emit_ware_subsource(
        old_root, new_root, ware_report, locale_old, locale_new, macro_report,
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


def _ware_key_fn(e: ElementTree.Element) -> Optional[str]:
    """Filter for ship/drone wares. Returns ware id or None."""
    if e.get('transport') == 'ship':
        return e.get('id')
    if 'ship' in (e.get('tags') or '').split():
        return e.get('id')
    if e.get('group') == 'drones':
        return e.get('id')
    return None


def _emit_macro_subsource(old_root: Path, new_root: Path,
                          locale_old: Locale, locale_new: Locale,
                          macro_report: _MacroReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rel, kind, old_bytes, new_bytes in diff_files(old_root, new_root,
                                                      MACRO_GLOBS):
        if kind == ChangeKind.ADDED:
            outputs.extend(_emit_macro_added(
                rel, new_bytes, locale_new, macro_report,
            ))
        elif kind == ChangeKind.DELETED:
            outputs.extend(_emit_macro_removed(
                rel, old_bytes, locale_old, macro_report,
            ))
        elif kind == ChangeKind.MODIFIED:
            outputs.extend(_emit_macro_modified(
                rel, old_bytes, new_bytes, locale_old, locale_new, macro_report,
            ))
    return outputs


def _parse_macro_bytes(data: bytes, rel: str,
                       macro_report: _MacroReport,
                       affected_keys: Optional[list] = None
                       ) -> Optional[ElementTree.Element]:
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
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as e:
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
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return Path(rel).stem
    macro = root.find('macro') if root.tag == 'macros' else (
        root if root.tag == 'macro' else None
    )
    if macro is None:
        return Path(rel).stem
    return macro.get('name') or Path(rel).stem


def _emit_macro_added(rel: str, new_bytes: bytes, locale_new: Locale,
                      macro_report: _MacroReport) -> list[RuleOutput]:
    macro = _parse_macro_bytes(new_bytes, rel, macro_report)
    macro_name = _macro_name_from_bytes(new_bytes, rel)
    source = source_of(rel)
    if macro is None:
        name = macro_name
        classifications: list[str] = []
    else:
        name = _macro_display_name(macro, locale_new, macro_name)
        classifications = _macro_classifications(macro)
    sources_label = render_sources(None, [source])
    text = format_row(TAG, name, classifications, sources_label, ['NEW'])
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


def _emit_macro_removed(rel: str, old_bytes: bytes, locale_old: Locale,
                        macro_report: _MacroReport) -> list[RuleOutput]:
    macro = _parse_macro_bytes(old_bytes, rel, macro_report)
    macro_name = _macro_name_from_bytes(old_bytes, rel)
    source = source_of(rel)
    if macro is None:
        name = macro_name
        classifications: list[str] = []
    else:
        name = _macro_display_name(macro, locale_old, macro_name)
        classifications = _macro_classifications(macro)
    sources_label = render_sources([source], None)
    text = format_row(TAG, name, classifications, sources_label, ['REMOVED'])
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
                         locale_old: Locale, locale_new: Locale,
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
        sources_label = render_sources([source], [source])
        text = format_row(TAG, name, [], sources_label, ['modified (parse error)'])
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

    name = _macro_display_name(new_macro, locale_new, macro_name) \
           or _macro_display_name(old_macro, locale_old, macro_name)
    classifications = _macro_classifications(new_macro) \
                      or _macro_classifications(old_macro)

    changes: list[str] = []
    for label, (old_value, new_value) in diff_attrs(old_macro, new_macro, MACRO_STATS).items():
        changes.append(f'{label} {old_value}→{new_value}')
    if not changes:
        return []

    sources_label = render_sources([source], [source])
    text = format_row(TAG, name, classifications, sources_label, changes)
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


def _macro_display_name(macro: ElementTree.Element, locale: Locale,
                        fallback: str) -> str:
    ident = macro.find('properties/identification')
    if ident is None:
        return fallback
    return resolve_attr_ref(ident, locale, attribute='name', fallback=fallback)


def _macro_classifications(macro: ElementTree.Element) -> list[str]:
    """Return [macro@class, ship@type] minus the generic filter."""
    tokens: list[str] = []
    classifications = macro.get('class')
    if classifications:
        tokens.append(classifications)
    ship = macro.find('properties/ship')
    if ship is not None:
        t = ship.get('type')
        if t:
            tokens.append(t)
    return [t for t in tokens if t not in _GENERIC_FILTER]


def _emit_ware_subsource(old_root: Path, new_root: Path, ware_report,
                         locale_old: Locale, locale_new: Locale,
                         macro_report: _MacroReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in ware_report.added:
        outputs.extend(_emit_ware_added(new_root, record, locale_new, macro_report))
    for record in ware_report.removed:
        outputs.extend(_emit_ware_removed(old_root, record, locale_old, macro_report))
    for record in ware_report.modified:
        outputs.extend(_emit_ware_modified(
            old_root, new_root, record, locale_old, locale_new, macro_report,
        ))
    return outputs


def _emit_ware_added(new_root: Path, record, locale_new: Locale,
                     macro_report: _MacroReport) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    macro, _ = _resolve_ware_macro(new_root, ware, record.ref_sources,
                                    ware_id, macro_report)
    name = _ware_display_name(ware, macro, locale_new, ware_id)
    classifications = _ware_classifications(ware)
    sources_label = render_sources(None, record.sources)
    text = format_row(TAG, name, classifications, sources_label, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ware', ware_id),
        'kind': 'added',
        'subsource': 'ware',
        'classifications': classifications,
        'ware_id': ware_id,
        'new_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_ware_removed(old_root: Path, record, locale_old: Locale,
                       macro_report: _MacroReport) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    macro, _ = _resolve_ware_macro(old_root, ware, record.ref_sources,
                                    ware_id, macro_report)
    name = _ware_display_name(ware, macro, locale_old, ware_id)
    classifications = _ware_classifications(ware)
    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ware', ware_id),
        'kind': 'removed',
        'subsource': 'ware',
        'classifications': classifications,
        'ware_id': ware_id,
        'old_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_ware_modified(old_root: Path, new_root: Path, record,
                        locale_old: Locale, locale_new: Locale,
                        macro_report: _MacroReport) -> list[RuleOutput]:
    ware_id = record.key
    new_macro, _ = _resolve_ware_macro(new_root, record.new, record.new_ref_sources,
                                        ware_id, macro_report)
    old_macro, _ = _resolve_ware_macro(old_root, record.old, record.old_ref_sources,
                                        ware_id, macro_report)
    name = _ware_display_name(record.new, new_macro, locale_new, ware_id)
    if name == ware_id:
        name = _ware_display_name(record.old, old_macro, locale_old, ware_id)
    classifications = _ware_classifications(record.new)

    changes: list[str] = []
    changes.extend(diff_labels(record.old, record.new, WARE_STATS))
    changes.extend(_owner_diff(record.old, record.new))
    changes.extend(_licence_diff(record.old, record.new))
    changes.extend(_production_diff(record.old, record.new))

    if not changes:
        return []

    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ware', ware_id),
        'kind': 'modified',
        'subsource': 'ware',
        'classifications': classifications,
        'ware_id': ware_id,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })]


def _resolve_ware_macro(root: Path, ware: ElementTree.Element,
                         ref_sources: dict, ware_id: str,
                         macro_report: _MacroReport
                         ) -> tuple[Optional[ElementTree.Element], Optional[Path]]:
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


def _ware_display_name(ware: ElementTree.Element, macro: Optional[ElementTree.Element],
                       locale: Locale, fallback: str) -> str:
    """Resolve name via macro's identification first, then ware @name."""
    if macro is not None:
        ident = macro.find('properties/identification')
        if ident is not None:
            val = resolve_attr_ref(ident, locale, attribute='name', fallback='')
            if val:
                return val
    return resolve_attr_ref(ware, locale, attribute='name', fallback=fallback)


def _ware_classifications(ware: ElementTree.Element) -> list[str]:
    """Return [transport, ...tags, licence] minus generic filter + deprecated.

    `_GENERIC_FILTER` applies to every token source (transport attribute and tag
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


def _owner_diff(old_ware: ElementTree.Element, new_ware: ElementTree.Element) -> list[str]:
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


def _licence_diff(old_ware: ElementTree.Element, new_ware: ElementTree.Element) -> list[str]:
    old_el = old_ware.find('restriction')
    new_el = new_ware.find('restriction')
    old_lic = old_el.get('licence') if old_el is not None else None
    new_lic = new_el.get('licence') if new_el is not None else None
    if old_lic == new_lic:
        return []
    return [f'licence {old_lic}→{new_lic}']


def _production_diff(old_ware: ElementTree.Element, new_ware: ElementTree.Element) -> list[str]:
    """Production diff for ship wares (delegates to `_wave1_common.diff_productions`)."""
    return diff_productions(old_ware, new_ware)


def _emit_role_subsource(role_report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in role_report.added:
        outputs.extend(_emit_role_added(record))
    for record in role_report.removed:
        outputs.extend(_emit_role_removed(record))
    for record in role_report.modified:
        outputs.extend(_emit_role_modified(record))
    return outputs


def _emit_role_added(record) -> list[RuleOutput]:
    role = record.element
    role_id = record.key
    classifications = _role_classifications(role)
    sources_label = render_sources(None, record.sources)
    text = format_row(TAG, role_id, classifications, sources_label, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('role', role_id),
        'kind': 'added',
        'subsource': 'role',
        'classifications': classifications,
        'role_id': role_id,
        'new_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_role_removed(record) -> list[RuleOutput]:
    role = record.element
    role_id = record.key
    classifications = _role_classifications(role)
    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, role_id, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('role', role_id),
        'kind': 'removed',
        'subsource': 'role',
        'classifications': classifications,
        'role_id': role_id,
        'old_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_role_modified(record) -> list[RuleOutput]:
    role_id = record.key
    classifications = _role_classifications(record.new)

    changes: list[str] = []
    for label, (old_value, new_value) in diff_attrs(record.old, record.new, ROLE_STATS).items():
        changes.append(f'{label} {old_value}→{new_value}')
    changes.extend(_pilot_select_diff(record.old, record.new))

    if not changes:
        return []

    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, role_id, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('role', role_id),
        'kind': 'modified',
        'subsource': 'role',
        'classifications': classifications,
        'role_id': role_id,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })]


def _role_classifications(role: ElementTree.Element) -> list[str]:
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


def _pilot_select_diff(old_role: ElementTree.Element, new_role: ElementTree.Element) -> list[str]:
    old_sel = old_role.find('pilot/select')
    new_sel = new_role.find('pilot/select')
    out: list[str] = []
    for attribute, label in (('faction', 'pilot_faction'), ('tags', 'pilot_tags')):
        old_value = old_sel.get(attribute) if old_sel is not None else None
        new_value = new_sel.get(attribute) if new_sel is not None else None
        if old_value != new_value:
            out.append(f'{label} {old_value}→{new_value}')
    return out


