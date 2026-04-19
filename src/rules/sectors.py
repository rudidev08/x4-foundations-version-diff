"""Sectors rule: emit outputs for map/galaxy/region changes.

Five USER-FACING sub-sources (`galaxy`, `map`, `highway`, `regionyield`,
`regiondef`) backed by EIGHT `diff_library` calls. The map/highway groups
concatenate multiple sibling files into one user-facing label; internally
each file carries its own distinct label so per-file failures cannot
contaminate sibling-file outputs via `forward_incomplete_many` scoping.

Internal labels (STABLE — part of the snapshot contract):
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
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.macro_diff import diff_attr_map
from src.lib.rule_output import RuleOutput, format_row, render_sources


TAG = 'sectors'


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


_GALAXY_ATTRS = ('ref', 'path')


def _galaxy_fields(conn: ElementTree.Element) -> dict[str, str]:
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
    position = conn.find('offset/position')
    if position is not None:
        out['offset.position'] = _triple(position)
    rot = conn.find('offset/rotation')
    if rot is not None:
        out['offset.rotation'] = _triple(rot)
    return out


def _triple(element: ElementTree.Element) -> str:
    """Render a position/rotation element as '(x=…, y=…, z=…)'."""
    parts = []
    for a in ('x', 'y', 'z', 'yaw', 'pitch', 'roll'):
        v = element.get(a)
        if v is not None:
            parts.append(f'{a}={v}')
    return '(' + ', '.join(parts) + ')'


def _emit_galaxy(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_galaxy_row(record.key, 'added',
                                   fields_new=_galaxy_fields(record.element),
                                   old_sources=None, new_sources=record.sources))
    for record in report.removed:
        outputs.append(_galaxy_row(record.key, 'removed',
                                   fields_old=_galaxy_fields(record.element),
                                   old_sources=record.sources, new_sources=None))
    for record in report.modified:
        old_f = _galaxy_fields(record.old)
        new_f = _galaxy_fields(record.new)
        changes = diff_attr_map(old_f, new_f)
        if not changes:
            continue
        outputs.append(_galaxy_row(record.key, 'modified',
                                   changes=changes,
                                   old_sources=record.old_sources,
                                   new_sources=record.new_sources))
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
    text = format_row(TAG, conn_name, ['galaxy'], sources_label, parts)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': (subsource, conn_name),
        'kind': kind,
        'subsource': subsource,
        'classifications': ['galaxy'],
        'connection_name': conn_name,
    })


def _macro_fields(macro: ElementTree.Element) -> dict[str, str]:
    """Collect macro-level diffable fields (class + component ref)."""
    out: dict[str, str] = {}
    if macro.get('class') is not None:
        out['class'] = macro.get('class')
    comp = macro.find('component')
    if comp is not None and comp.get('ref') is not None:
        out['component.ref'] = comp.get('ref')
    return out


def _connection_fields(conn: ElementTree.Element) -> dict[str, str]:
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
    position = conn.find('offset/position')
    if position is not None:
        out['offset.position'] = _triple(position)
    rot = conn.find('offset/rotation')
    if rot is not None:
        out['offset.rotation'] = _triple(rot)
    return out


def _index_connections(macro: ElementTree.Element) -> dict[str, ElementTree.Element]:
    """{connection_name: element} for direct <connections><connection> children."""
    out: dict[str, ElementTree.Element] = {}
    for conn in macro.findall('connections/connection'):
        name = conn.get('name')
        if name is not None:
            out[name] = conn
    return out


def _emit_macro_group(report, internal_label: str, user_facing: str,
                      child_keys_by_parent: ChildKeys) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.extend(_emit_macro_added(record, internal_label, user_facing,
                                         child_keys_by_parent))
    for record in report.removed:
        outputs.extend(_emit_macro_removed(record, internal_label, user_facing,
                                           child_keys_by_parent))
    for record in report.modified:
        outputs.extend(_emit_macro_modified(record, internal_label, user_facing,
                                            child_keys_by_parent))
    return outputs


def _macro_classifications(user_facing: str, macro: ElementTree.Element) -> list[str]:
    """Classifications: [user_facing_label, @class] with class dropped if empty."""
    out = [user_facing]
    class_attr = macro.get('class')
    if class_attr:
        out.append(class_attr)
    return out


def _emit_macro_added(record, internal_label: str, user_facing: str,
                      child_keys_by_parent: ChildKeys) -> list[RuleOutput]:
    macro = record.element
    parent_name = record.key
    classifications = _macro_classifications(user_facing, macro)
    sources_label = render_sources(None, record.sources)
    parts = ['NEW']
    child_count = len(macro.findall('connections/connection'))
    if child_count:
        parts.append(f'{child_count} connections')
    parent_row = RuleOutput(
        tag=TAG,
        text=format_row(TAG, parent_name, classifications, sources_label, parts),
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
            text=format_row(TAG, 
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


def _emit_macro_removed(record, internal_label: str, user_facing: str,
                        child_keys_by_parent: ChildKeys) -> list[RuleOutput]:
    macro = record.element
    parent_name = record.key
    classifications = _macro_classifications(user_facing, macro)
    sources_label = render_sources(record.sources, None)
    parts = ['REMOVED']
    parent_row = RuleOutput(
        tag=TAG,
        text=format_row(TAG, parent_name, classifications, sources_label, parts),
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
            text=format_row(TAG, 
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


def _emit_macro_modified(record, internal_label: str, user_facing: str,
                         child_keys_by_parent: ChildKeys) -> list[RuleOutput]:
    parent_name = record.key
    classifications = _macro_classifications(user_facing, record.new)
    sources_label = render_sources(record.old_sources, record.new_sources)

    macro_changes = diff_attr_map(_macro_fields(record.old), _macro_fields(record.new))

    old_children = _index_connections(record.old)
    new_children = _index_connections(record.new)

    rows: list[RuleOutput] = []
    if macro_changes:
        rows.append(RuleOutput(
            tag=TAG,
            text=format_row(TAG, parent_name, classifications, sources_label,
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
                text=format_row(TAG, 
                    f'{parent_name}/{conn_name}', conn_classifications,
                    render_sources(None, record.new_sources), parts),
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
                text=format_row(TAG, 
                    f'{parent_name}/{conn_name}', conn_classifications,
                    render_sources(record.old_sources, None), ['REMOVED']),
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
            changes = diff_attr_map(_connection_fields(old_conn),
                                   _connection_fields(new_conn))
            if not changes:
                continue
            rows.append(RuleOutput(
                tag=TAG,
                text=format_row(TAG, 
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


_REGIONYIELD_ATTRS = (
    'tag', 'ware', 'respawndelay', 'yield', 'rating',
    'objectyieldfactor', 'scaneffectcolor', 'gatherspeedfactor',
)


def _regionyield_fields(element: ElementTree.Element) -> dict[str, str]:
    return {a: element.get(a) for a in _REGIONYIELD_ATTRS if element.get(a) is not None}


def _emit_regionyield(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_make_row_simple(
            record.key, 'added', 'regionyield', ['regionyield'],
            fields_new=_regionyield_fields(record.element),
            old_sources=None, new_sources=record.sources))
    for record in report.removed:
        outputs.append(_make_row_simple(
            record.key, 'removed', 'regionyield', ['regionyield'],
            fields_old=_regionyield_fields(record.element),
            old_sources=record.sources, new_sources=None))
    for record in report.modified:
        changes = diff_attr_map(_regionyield_fields(record.old),
                               _regionyield_fields(record.new))
        if not changes:
            continue
        outputs.append(_make_row_simple(
            record.key, 'modified', 'regionyield', ['regionyield'],
            changes=changes,
            old_sources=record.old_sources, new_sources=record.new_sources))
    return outputs


_REGIONDEF_ATTRS = (
    'density', 'rotation', 'noisescale', 'seed',
    'minnoisevalue', 'maxnoisevalue',
)


def _regiondef_fields(region: ElementTree.Element) -> dict[str, str]:
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


def _triple_size(size: ElementTree.Element) -> str:
    parts = []
    for a in ('r', 'linear', 'x', 'y', 'z'):
        v = size.get(a)
        if v is not None:
            parts.append(f'{a}={v}')
    return '(' + ', '.join(parts) + ')'


def _emit_regiondef(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_make_row_simple(
            record.key, 'added', 'regiondef', ['regiondef'],
            fields_new=_regiondef_fields(record.element),
            old_sources=None, new_sources=record.sources))
    for record in report.removed:
        outputs.append(_make_row_simple(
            record.key, 'removed', 'regiondef', ['regiondef'],
            fields_old=_regiondef_fields(record.element),
            old_sources=record.sources, new_sources=None))
    for record in report.modified:
        changes = diff_attr_map(_regiondef_fields(record.old),
                               _regiondef_fields(record.new))
        if not changes:
            continue
        outputs.append(_make_row_simple(
            record.key, 'modified', 'regiondef', ['regiondef'],
            changes=changes,
            old_sources=record.old_sources, new_sources=record.new_sources))
    return outputs


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
    text = format_row(TAG, key, classifications, sources_label, parts)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': (internal_label, key),
        'kind': kind,
        'subsource': internal_label,
        'classifications': classifications,
    })


    return f'[{TAG}] {name}{classifications_text}{source}: {", ".join(parts)}'
