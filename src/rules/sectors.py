"""Sectors rule: emit outputs for map/galaxy/region changes.

Five USER-FACING sub-sources (`galaxy`, `map`, `highway`, `regionyield`,
`regiondef`) backed by EIGHT `diff_library` calls. The map/highway groups
concatenate multiple sibling files into one user-facing label; internally
each file carries its own distinct label so per-file failures cannot
contaminate sibling-file outputs via `forward_incomplete_many` scoping.

Internal labels (STABLE — part of the Tier B snapshot contract):
- `galaxy`       maps/xu_ep2_universe/galaxy.xml
- `map_clusters` maps/xu_ep2_universe/clusters.xml
- `map_sectors`  maps/xu_ep2_universe/sectors.xml
- `map_zones`    maps/xu_ep2_universe/zones.xml
- `highway_sec`  maps/xu_ep2_universe/sechighways.xml
- `highway_zone` maps/xu_ep2_universe/zonehighways.xml
- `regionyield`  libraries/regionyields.xml
- `regiondef`    libraries/region_definitions.xml

For map/highway parent `<macro>` entries the rule emits one parent row plus
one row per `<connection>` child (added / removed / modified). Per-connection
keys are 3-tuples `(internal_label, parent_macro_name, connection_name)`.
Before forwarding incomplete contamination, parent-only `affected_keys` from
the diff report are expanded to include every child key the rule emitted
under that parent so per-connection rows don't silently escape the
contamination mark.

See `src/rules/sectors.md` for the stability contract.
"""
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'sectors'


# --- ChildKeys registry --------------------------------------------------

ChildKeys = dict[tuple[str, str], list[tuple]]


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit sector/map rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused — rule runs entirely
    off `diff_library` on the 8 tracked files.
    """
    outputs: list[RuleOutput] = []
    reports: list[tuple] = []  # list of (report, internal_label)
    child_keys_by_parent: ChildKeys = {}

    # 1. Galaxy file: flat <connection> list under a single <macro>.
    galaxy_report = diff_library(
        old_root, new_root, 'maps/xu_ep2_universe/galaxy.xml',
        './/connection',
        key_fn=lambda e: e.get('name'),
        key_fn_identity='sectors_galaxy',
    )
    outputs.extend(_emit_galaxy(galaxy_report))
    reports.append((galaxy_report, 'galaxy'))

    # 2-4. Map family: three parallel files grouped under user-facing 'map'.
    for fname, label in [('clusters.xml', 'map_clusters'),
                         ('sectors.xml', 'map_sectors'),
                         ('zones.xml', 'map_zones')]:
        report = diff_library(
            old_root, new_root, f'maps/xu_ep2_universe/{fname}',
            './/macro',
            key_fn=lambda e: e.get('name'),
            key_fn_identity=f'sectors_{label}',
        )
        outputs.extend(_emit_macro_group(report, label, 'map',
                                         child_keys_by_parent))
        reports.append((report, label))

    # 5-6. Highway family: two parallel files grouped under 'highway'.
    for fname, label in [('sechighways.xml', 'highway_sec'),
                         ('zonehighways.xml', 'highway_zone')]:
        report = diff_library(
            old_root, new_root, f'maps/xu_ep2_universe/{fname}',
            './/macro',
            key_fn=lambda e: e.get('name'),
            key_fn_identity=f'sectors_{label}',
        )
        outputs.extend(_emit_macro_group(report, label, 'highway',
                                         child_keys_by_parent))
        reports.append((report, label))

    # 7. Region yields (9.x shape: <definition id=...>).
    ry_report = diff_library(
        old_root, new_root, 'libraries/regionyields.xml',
        './/definition',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='sectors_regionyield',
    )
    outputs.extend(_emit_regionyield(ry_report))
    reports.append((ry_report, 'regionyield'))

    # 8. Region definitions.
    rd_report = diff_library(
        old_root, new_root, 'libraries/region_definitions.xml',
        './/region',
        key_fn=lambda e: e.get('name'),
        key_fn_identity='sectors_regiondef',
    )
    outputs.extend(_emit_regiondef(rd_report))
    reports.append((rd_report, 'regiondef'))

    # Per-connection contamination expansion: parent-only `affected_keys`
    # (bare parent macro name) get enriched with every child 3-tuple key the
    # rule emitted under that parent. Without this, per-connection rows under
    # a broken parent stay marked complete — a silent-changes hole.
    #
    # Mutation of the cached report's failures is idempotent: re-running the
    # rule against the same cached report would only re-append the same
    # child keys (dedup check below). `forward_incomplete` only cares about
    # membership, not counts.
    for report, sub_label in reports:
        for _text, extras in report.failures:
            ak = list(extras.get('affected_keys') or [])
            parents_in_scope = list(ak)
            for parent_name in parents_in_scope:
                for child_key in child_keys_by_parent.get(
                        (sub_label, parent_name), []):
                    if child_key not in ak:
                        ak.append(child_key)
            extras['affected_keys'] = ak

    forward_incomplete_many(reports, outputs, tag=TAG)
    for report, _ in reports:
        forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


# --- Emitters: galaxy ----------------------------------------------------


_GALAXY_ATTRS = ('ref', 'path')


def _galaxy_fields(conn: ET.Element) -> dict[str, str]:
    """Collect diffable fields from a <connection> under galaxy.xml.

    - Top-level @ref / @path
    - <macro @ref> + @connection + @path
    - <offset><position/> and <offset><rotation/> as stringified tuples
    """
    out: dict[str, str] = {}
    for a in _GALAXY_ATTRS:
        if conn.get(a) is not None:
            out[a] = conn.get(a)
    macro = conn.find('macro')
    if macro is not None:
        for a in ('ref', 'connection', 'path'):
            if macro.get(a) is not None:
                out[f'macro.{a}'] = macro.get(a)
    pos = conn.find('offset/position')
    if pos is not None:
        out['offset.position'] = _triple(pos)
    rot = conn.find('offset/rotation')
    if rot is not None:
        out['offset.rotation'] = _triple(rot)
    return out


def _triple(el: ET.Element) -> str:
    """Render a position/rotation element as '(x=…, y=…, z=…)'."""
    parts = []
    for a in ('x', 'y', 'z', 'yaw', 'pitch', 'roll'):
        v = el.get(a)
        if v is not None:
            parts.append(f'{a}={v}')
    return '(' + ', '.join(parts) + ')'


def _diff_fields(old_fields: dict, new_fields: dict) -> list[str]:
    """Return a list of 'label OV→NV' (added/removed/changed)."""
    out: list[str] = []
    for k in sorted(set(old_fields) | set(new_fields)):
        ov = old_fields.get(k)
        nv = new_fields.get(k)
        if ov == nv:
            continue
        out.append(f'{k} {ov}→{nv}')
    return out


def _emit_galaxy(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_galaxy_row(rec.key, 'added',
                                   fields_new=_galaxy_fields(rec.element),
                                   old_sources=None, new_sources=rec.sources))
    for rec in report.removed:
        outputs.append(_galaxy_row(rec.key, 'removed',
                                   fields_old=_galaxy_fields(rec.element),
                                   old_sources=rec.sources, new_sources=None))
    for rec in report.modified:
        old_f = _galaxy_fields(rec.old)
        new_f = _galaxy_fields(rec.new)
        changes = _diff_fields(old_f, new_f)
        if not changes:
            continue
        outputs.append(_galaxy_row(rec.key, 'modified',
                                   changes=changes,
                                   old_sources=rec.old_sources,
                                   new_sources=rec.new_sources))
    return outputs


def _galaxy_row(conn_name: str, kind: str,
                fields_old: Optional[dict] = None,
                fields_new: Optional[dict] = None,
                changes: Optional[list[str]] = None,
                old_sources=None, new_sources=None) -> RuleOutput:
    subsource = 'galaxy'
    if kind == 'added':
        parts = ['NEW']
        target = fields_new.get('macro.ref') if fields_new else None
        if target:
            parts.append(f'macro.ref={target}')
    elif kind == 'removed':
        parts = ['REMOVED']
        target = fields_old.get('macro.ref') if fields_old else None
        if target:
            parts.append(f'macro.ref={target}')
    else:  # modified
        parts = list(changes or [])
    sources_label = render_sources(old_sources, new_sources)
    text = _format_row(conn_name, ['galaxy'], sources_label, parts)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': (subsource, conn_name),
        'kind': kind,
        'subsource': subsource,
        'classifications': ['galaxy'],
        'connection_name': conn_name,
    })


# --- Emitters: map / highway --------------------------------------------


def _macro_fields(macro: ET.Element) -> dict[str, str]:
    """Collect macro-level diffable fields (class + component ref)."""
    out: dict[str, str] = {}
    if macro.get('class') is not None:
        out['class'] = macro.get('class')
    comp = macro.find('component')
    if comp is not None and comp.get('ref') is not None:
        out['component.ref'] = comp.get('ref')
    return out


def _connection_fields(conn: ET.Element) -> dict[str, str]:
    """Collect diffable fields from a <connection> under a map/highway macro."""
    out: dict[str, str] = {}
    for a in ('ref', 'path'):
        if conn.get(a) is not None:
            out[a] = conn.get(a)
    macro = conn.find('macro')
    if macro is not None:
        for a in ('ref', 'connection', 'path'):
            if macro.get(a) is not None:
                out[f'macro.{a}'] = macro.get(a)
    pos = conn.find('offset/position')
    if pos is not None:
        out['offset.position'] = _triple(pos)
    rot = conn.find('offset/rotation')
    if rot is not None:
        out['offset.rotation'] = _triple(rot)
    return out


def _index_connections(macro: ET.Element) -> dict[str, ET.Element]:
    """{connection_name: element} for direct <connections><connection> children."""
    out: dict[str, ET.Element] = {}
    for conn in macro.findall('connections/connection'):
        name = conn.get('name')
        if name is not None:
            out[name] = conn
    return out


def _emit_macro_group(report, internal_label: str, user_facing: str,
                      child_keys_by_parent: ChildKeys) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.extend(_emit_macro_added(rec, internal_label, user_facing,
                                         child_keys_by_parent))
    for rec in report.removed:
        outputs.extend(_emit_macro_removed(rec, internal_label, user_facing,
                                           child_keys_by_parent))
    for rec in report.modified:
        outputs.extend(_emit_macro_modified(rec, internal_label, user_facing,
                                            child_keys_by_parent))
    return outputs


def _macro_classifications(user_facing: str, macro: ET.Element) -> list[str]:
    """Classifications: [user_facing_label, @class] with class dropped if empty."""
    out = [user_facing]
    cls = macro.get('class')
    if cls:
        out.append(cls)
    return out


def _emit_macro_added(rec, internal_label: str, user_facing: str,
                      child_keys_by_parent: ChildKeys) -> list[RuleOutput]:
    macro = rec.element
    parent_name = rec.key
    classifications = _macro_classifications(user_facing, macro)
    sources_label = render_sources(None, rec.sources)
    parts = ['NEW']
    child_count = len(macro.findall('connections/connection'))
    if child_count:
        parts.append(f'{child_count} connections')
    parent_row = RuleOutput(
        tag=TAG,
        text=_format_row(parent_name, classifications, sources_label, parts),
        extras={
            'entity_key': (internal_label, parent_name),
            'kind': 'added',
            'subsource': internal_label,
            'classifications': classifications,
            'parent_name': parent_name,
        },
    )
    rows = [parent_row]

    # Emit per-connection child rows (all added).
    children = _index_connections(macro)
    child_list = child_keys_by_parent.setdefault((internal_label, parent_name),
                                                 [])
    for conn_name, conn in sorted(children.items()):
        child_key = (internal_label, parent_name, conn_name)
        child_list.append(child_key)
        child_parts = ['NEW']
        conn_target = (conn.find('macro').get('ref')
                       if conn.find('macro') is not None
                       and conn.find('macro').get('ref') is not None
                       else None)
        if conn_target:
            child_parts.append(f'macro.ref={conn_target}')
        rows.append(RuleOutput(
            tag=TAG,
            text=_format_row(
                f'{parent_name}/{conn_name}',
                classifications + ['connection'], sources_label, child_parts),
            extras={
                'entity_key': child_key,
                'kind': 'added',
                'subsource': internal_label,
                'classifications': classifications + ['connection'],
                'parent_name': parent_name,
                'connection_name': conn_name,
            },
        ))
    return rows


def _emit_macro_removed(rec, internal_label: str, user_facing: str,
                        child_keys_by_parent: ChildKeys) -> list[RuleOutput]:
    macro = rec.element
    parent_name = rec.key
    classifications = _macro_classifications(user_facing, macro)
    sources_label = render_sources(rec.sources, None)
    parts = ['REMOVED']
    parent_row = RuleOutput(
        tag=TAG,
        text=_format_row(parent_name, classifications, sources_label, parts),
        extras={
            'entity_key': (internal_label, parent_name),
            'kind': 'removed',
            'subsource': internal_label,
            'classifications': classifications,
            'parent_name': parent_name,
        },
    )
    rows = [parent_row]

    children = _index_connections(macro)
    child_list = child_keys_by_parent.setdefault((internal_label, parent_name),
                                                 [])
    for conn_name in sorted(children.keys()):
        child_key = (internal_label, parent_name, conn_name)
        child_list.append(child_key)
        rows.append(RuleOutput(
            tag=TAG,
            text=_format_row(
                f'{parent_name}/{conn_name}',
                classifications + ['connection'], sources_label, ['REMOVED']),
            extras={
                'entity_key': child_key,
                'kind': 'removed',
                'subsource': internal_label,
                'classifications': classifications + ['connection'],
                'parent_name': parent_name,
                'connection_name': conn_name,
            },
        ))
    return rows


def _emit_macro_modified(rec, internal_label: str, user_facing: str,
                         child_keys_by_parent: ChildKeys) -> list[RuleOutput]:
    parent_name = rec.key
    classifications = _macro_classifications(user_facing, rec.new)
    sources_label = render_sources(rec.old_sources, rec.new_sources)

    macro_changes = _diff_fields(_macro_fields(rec.old), _macro_fields(rec.new))

    old_children = _index_connections(rec.old)
    new_children = _index_connections(rec.new)

    rows: list[RuleOutput] = []
    if macro_changes:
        rows.append(RuleOutput(
            tag=TAG,
            text=_format_row(parent_name, classifications, sources_label,
                             macro_changes),
            extras={
                'entity_key': (internal_label, parent_name),
                'kind': 'modified',
                'subsource': internal_label,
                'classifications': classifications,
                'parent_name': parent_name,
            },
        ))

    child_list = child_keys_by_parent.setdefault((internal_label, parent_name),
                                                 [])
    all_names = sorted(set(old_children) | set(new_children))
    for conn_name in all_names:
        child_key = (internal_label, parent_name, conn_name)
        child_list.append(child_key)
        old_conn = old_children.get(conn_name)
        new_conn = new_children.get(conn_name)
        conn_classifications = classifications + ['connection']
        if old_conn is None and new_conn is not None:
            parts = ['NEW']
            target = _connection_fields(new_conn).get('macro.ref')
            if target:
                parts.append(f'macro.ref={target}')
            rows.append(RuleOutput(
                tag=TAG,
                text=_format_row(
                    f'{parent_name}/{conn_name}', conn_classifications,
                    render_sources(None, rec.new_sources), parts),
                extras={
                    'entity_key': child_key,
                    'kind': 'added',
                    'subsource': internal_label,
                    'classifications': conn_classifications,
                    'parent_name': parent_name,
                    'connection_name': conn_name,
                },
            ))
        elif old_conn is not None and new_conn is None:
            rows.append(RuleOutput(
                tag=TAG,
                text=_format_row(
                    f'{parent_name}/{conn_name}', conn_classifications,
                    render_sources(rec.old_sources, None), ['REMOVED']),
                extras={
                    'entity_key': child_key,
                    'kind': 'removed',
                    'subsource': internal_label,
                    'classifications': conn_classifications,
                    'parent_name': parent_name,
                    'connection_name': conn_name,
                },
            ))
        else:
            changes = _diff_fields(_connection_fields(old_conn),
                                   _connection_fields(new_conn))
            if not changes:
                continue
            rows.append(RuleOutput(
                tag=TAG,
                text=_format_row(
                    f'{parent_name}/{conn_name}', conn_classifications,
                    sources_label, changes),
                extras={
                    'entity_key': child_key,
                    'kind': 'modified',
                    'subsource': internal_label,
                    'classifications': conn_classifications,
                    'parent_name': parent_name,
                    'connection_name': conn_name,
                },
            ))
    return rows


# --- Emitters: regionyield ----------------------------------------------


_REGIONYIELD_ATTRS = (
    'tag', 'ware', 'respawndelay', 'yield', 'rating',
    'objectyieldfactor', 'scaneffectcolor', 'gatherspeedfactor',
)


def _regionyield_fields(el: ET.Element) -> dict[str, str]:
    return {a: el.get(a) for a in _REGIONYIELD_ATTRS if el.get(a) is not None}


def _emit_regionyield(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_make_row_simple(
            rec.key, 'added', 'regionyield', ['regionyield'],
            fields_new=_regionyield_fields(rec.element),
            old_sources=None, new_sources=rec.sources))
    for rec in report.removed:
        outputs.append(_make_row_simple(
            rec.key, 'removed', 'regionyield', ['regionyield'],
            fields_old=_regionyield_fields(rec.element),
            old_sources=rec.sources, new_sources=None))
    for rec in report.modified:
        changes = _diff_fields(_regionyield_fields(rec.old),
                               _regionyield_fields(rec.new))
        if not changes:
            continue
        outputs.append(_make_row_simple(
            rec.key, 'modified', 'regionyield', ['regionyield'],
            changes=changes,
            old_sources=rec.old_sources, new_sources=rec.new_sources))
    return outputs


# --- Emitters: regiondef ------------------------------------------------


_REGIONDEF_ATTRS = (
    'density', 'rotation', 'noisescale', 'seed',
    'minnoisevalue', 'maxnoisevalue',
)


def _regiondef_fields(region: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {a: region.get(a) for a in _REGIONDEF_ATTRS
                           if region.get(a) is not None}
    boundary = region.find('boundary')
    if boundary is not None:
        bcls = boundary.get('class')
        if bcls:
            out['boundary.class'] = bcls
        size = boundary.find('size')
        if size is not None:
            out['boundary.size'] = _triple_size(size)
    falloff = region.find('falloff')
    if falloff is not None:
        # Stringify each ordered step list per child axis (lateral/radial/...).
        for axis in falloff:
            steps = []
            for step in axis.findall('step'):
                steps.append(f'{step.get("position")}={step.get("value")}')
            out[f'falloff.{axis.tag}'] = '[' + ', '.join(steps) + ']'
    fields = region.find('fields')
    if fields is not None:
        refs = []
        for child in fields:
            ref = child.get('ref') or child.get('groupref') or child.tag
            refs.append(f'{child.tag}:{ref}')
        out['fields'] = '[' + ', '.join(sorted(refs)) + ']'
    return out


def _triple_size(size: ET.Element) -> str:
    parts = []
    for a in ('r', 'linear', 'x', 'y', 'z'):
        v = size.get(a)
        if v is not None:
            parts.append(f'{a}={v}')
    return '(' + ', '.join(parts) + ')'


def _emit_regiondef(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_make_row_simple(
            rec.key, 'added', 'regiondef', ['regiondef'],
            fields_new=_regiondef_fields(rec.element),
            old_sources=None, new_sources=rec.sources))
    for rec in report.removed:
        outputs.append(_make_row_simple(
            rec.key, 'removed', 'regiondef', ['regiondef'],
            fields_old=_regiondef_fields(rec.element),
            old_sources=rec.sources, new_sources=None))
    for rec in report.modified:
        changes = _diff_fields(_regiondef_fields(rec.old),
                               _regiondef_fields(rec.new))
        if not changes:
            continue
        outputs.append(_make_row_simple(
            rec.key, 'modified', 'regiondef', ['regiondef'],
            changes=changes,
            old_sources=rec.old_sources, new_sources=rec.new_sources))
    return outputs


# --- Simple-row helper ----------------------------------------------------


def _make_row_simple(key: str, kind: str, internal_label: str,
                     classifications: list[str],
                     fields_old: Optional[dict] = None,
                     fields_new: Optional[dict] = None,
                     changes: Optional[list[str]] = None,
                     old_sources=None, new_sources=None) -> RuleOutput:
    if kind == 'added':
        parts = ['NEW']
        if fields_new:
            parts.extend(f'{k}={v}' for k, v in sorted(fields_new.items()))
    elif kind == 'removed':
        parts = ['REMOVED']
    else:
        parts = list(changes or [])
    sources_label = render_sources(old_sources, new_sources)
    text = _format_row(key, classifications, sources_label, parts)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': (internal_label, key),
        'kind': kind,
        'subsource': internal_label,
        'classifications': classifications,
    })


def _format_row(name: str, classifications: list[str], sources_label: str,
                parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    source = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{source}: {", ".join(parts)}'
