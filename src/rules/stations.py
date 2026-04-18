"""Stations rule: emit outputs for station/stationgroup/module/modulegroup/constructionplan changes.

Five sub-sources share the `stations` tag, distinguished by `extras.subsource`:

- `station` — `libraries/stations.xml`, xpath `.//station`, key `(subsource, @id)`.
- `stationgroup` — `libraries/stationgroups.xml`, xpath `.//group`,
  key `(subsource, @name)`.
- `module` — `libraries/modules.xml`, xpath `.//module`, key `(subsource, @id)`.
- `modulegroup` — `libraries/modulegroups.xml`, xpath `.//group`,
  key `(subsource, @name)`.
- `constructionplan` — `libraries/constructionplans.xml`, xpath `.//plan`,
  key `(subsource, @id)`.

Cross-entity ref graph (validated every hop):

- station.group_ref → stationgroup @name
- stationgroup.plan_refs → constructionplan @id (via `<select @constructionplan>`)
- modulegroup.module_macro_refs → module @id (via `<select @macro>`)
- constructionplan.entry_* → module @id OR modulegroup @name (typed via runtime
  inspection of `<entry @macro>` values against both loaded sets)

Unresolved refs surface as `forward_warnings` with reason `'ref_target_unresolved'`.
Constructionplan entries whose `@macro` value matches both a module @id AND a
modulegroup @name surface as `extras.incomplete=True` with reason
`'ref_namespace_collision'`.

Validation indices come from the public `DiffReport.effective_{old,new}_root`
surface — never the private `_materialize`.

See `src/rules/stations.md` for the stability contract.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'stations'

# Generic tokens stripped from classifications so they stay meaningful.
_GENERIC_FILTER = frozenset({'station', 'module', 'stationgroup', 'modulegroup',
                             'constructionplan'})


# ---------- synthetic report wrapper for extra (rule-level) failures ----------


@dataclass
class _ExtraFailuresReport:
    """Wraps rule-synthesized failures (e.g., namespace collisions) so they
    flow through `forward_incomplete_many` uniformly with `diff_library`
    reports.
    """
    failures: list[tuple[str, dict]] = field(default_factory=list)
    warnings: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


# ---------- top-level entry ----------


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit stations rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused — rule drives itself off
    five `diff_library` calls.
    """
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    station_report = diff_library(
        old_root, new_root, 'libraries/stations.xml', './/station',
        key_fn=lambda e: e.get('id'), key_fn_identity='stations_station',
    )
    sg_report = diff_library(
        old_root, new_root, 'libraries/stationgroups.xml', './/group',
        key_fn=lambda e: e.get('name'), key_fn_identity='stations_stationgroup',
    )
    mod_report = diff_library(
        old_root, new_root, 'libraries/modules.xml', './/module',
        key_fn=lambda e: e.get('id'), key_fn_identity='stations_module',
    )
    mg_report = diff_library(
        old_root, new_root, 'libraries/modulegroups.xml', './/group',
        key_fn=lambda e: e.get('name'), key_fn_identity='stations_modulegroup',
    )
    plan_report = diff_library(
        old_root, new_root, 'libraries/constructionplans.xml', './/plan',
        key_fn=lambda e: e.get('id'), key_fn_identity='stations_constructionplan',
    )

    # Build validation indices from effective trees (public DiffReport surface).
    new_module_ids = _collect_ids(mod_report.effective_new_root, 'module', 'id')
    old_module_ids = _collect_ids(mod_report.effective_old_root, 'module', 'id')
    new_modulegroup_names = _collect_ids(mg_report.effective_new_root, 'group', 'name')
    old_modulegroup_names = _collect_ids(mg_report.effective_old_root, 'group', 'name')
    new_stationgroup_names = _collect_ids(
        sg_report.effective_new_root, 'group', 'name')
    old_stationgroup_names = _collect_ids(
        sg_report.effective_old_root, 'group', 'name')
    new_plan_ids = _collect_ids(plan_report.effective_new_root, 'plan', 'id')
    old_plan_ids = _collect_ids(plan_report.effective_old_root, 'plan', 'id')

    # Rule-synthesized extra failures/warnings, routed per sub-source.
    extras_reports = {
        'station': _ExtraFailuresReport(),
        'stationgroup': _ExtraFailuresReport(),
        'module': _ExtraFailuresReport(),
        'modulegroup': _ExtraFailuresReport(),
        'constructionplan': _ExtraFailuresReport(),
    }

    outputs.extend(_emit_station_subsource(
        station_report, loc_old, loc_new,
        old_stationgroup_names, new_stationgroup_names,
        extras_reports['station'],
    ))
    outputs.extend(_emit_stationgroup_subsource(
        sg_report, old_plan_ids, new_plan_ids,
        extras_reports['stationgroup'],
    ))
    outputs.extend(_emit_module_subsource(
        mod_report, loc_old, loc_new,
        extras_reports['module'],
    ))
    outputs.extend(_emit_modulegroup_subsource(
        mg_report, old_module_ids, new_module_ids,
        extras_reports['modulegroup'],
    ))
    outputs.extend(_emit_constructionplan_subsource(
        plan_report,
        old_module_ids, new_module_ids,
        old_modulegroup_names, new_modulegroup_names,
        extras_reports['constructionplan'],
    ))

    # Per-subsource contamination scoping.
    forward_incomplete_many(
        [
            (_MergedReport(station_report, extras_reports['station']), 'station'),
            (_MergedReport(sg_report, extras_reports['stationgroup']), 'stationgroup'),
            (_MergedReport(mod_report, extras_reports['module']), 'module'),
            (_MergedReport(mg_report, extras_reports['modulegroup']), 'modulegroup'),
            (_MergedReport(plan_report, extras_reports['constructionplan']),
             'constructionplan'),
        ],
        outputs, tag=TAG,
    )
    for r in (station_report, sg_report, mod_report, mg_report, plan_report):
        forward_warnings(r.warnings, outputs, tag=TAG)
    for er in extras_reports.values():
        forward_warnings(er.warnings, outputs, tag=TAG)
    return outputs


@dataclass
class _MergedReport:
    """Union a DiffReport's failures with rule-synthesized extra failures so
    `forward_incomplete` sees one combined list. Warnings stay on the
    DiffReport (rule-synthesized warnings are forwarded separately).
    """
    base: object
    extra: _ExtraFailuresReport

    @property
    def incomplete(self) -> bool:
        return bool(getattr(self.base, 'incomplete', False)) or self.extra.incomplete

    @property
    def failures(self) -> list[tuple[str, dict]]:
        return list(getattr(self.base, 'failures', []) or []) \
               + list(self.extra.failures)

    @property
    def warnings(self) -> list[tuple[str, dict]]:
        return list(getattr(self.base, 'warnings', []) or [])


def _collect_ids(root: Optional[ET.Element], tag: str, attr: str) -> set[str]:
    """Collect the set of `@attr` values on every `<tag>` under root.

    `None` or empty values are skipped.
    """
    if root is None:
        return set()
    out: set[str] = set()
    for el in root.iter(tag):
        v = el.get(attr)
        if v:
            out.add(v)
    return out


# ---------- station sub-source ----------


def _emit_station_subsource(report, loc_old: Locale, loc_new: Locale,
                             old_sg_names: set[str], new_sg_names: set[str],
                             extras: _ExtraFailuresReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_emit_station_row(
            rec, side='new', loc=loc_new,
            sg_names=new_sg_names, extras=extras,
        ))
    for rec in report.removed:
        outputs.append(_emit_station_row(
            rec, side='old', loc=loc_old,
            sg_names=old_sg_names, extras=extras,
        ))
    for rec in report.modified:
        out = _emit_station_modified(rec, loc_new, new_sg_names, extras)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_station_row(rec, side: str, loc: Locale,
                      sg_names: set[str],
                      extras: _ExtraFailuresReport) -> RuleOutput:
    """Added/removed station row."""
    station = rec.element
    station_id = rec.key
    classifications = _station_classifications(station)
    group_ref = station.get('group')
    refs = {'group_ref': group_ref} if group_ref else {}
    station_group_unresolved = bool(
        group_ref and group_ref not in sg_names)
    if station_group_unresolved:
        refs['station_group_unresolved'] = True
        extras.warnings.append((
            f'station {station_id}: group_ref {group_ref!r} unresolved',
            {'reason': 'ref_target_unresolved',
             'ref_kind': 'station_group',
             'entity_key': ('station', station_id),
             'affected_keys': [('station', station_id)]},
        ))

    kind = 'added' if side == 'new' else 'removed'
    if kind == 'added':
        srcs = render_sources(None, rec.sources)
        parts = ['NEW']
    else:
        srcs = render_sources(rec.sources, None)
        parts = ['REMOVED']
    text = _format(station_id, classifications, srcs, parts)
    ek = ('station', station_id)
    out_extras = {
        'entity_key': ek,
        'kind': kind,
        'subsource': 'station',
        'classifications': classifications,
        'station_id': station_id,
        'refs': refs,
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(rec.sources)
    else:
        out_extras['old_sources'] = list(rec.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_station_modified(rec, loc_new: Locale,
                            new_sg_names: set[str],
                            extras: _ExtraFailuresReport) -> Optional[RuleOutput]:
    station_id = rec.key
    classifications = _station_classifications(rec.new)
    changes: list[str] = []
    # @group diff.
    ov_group = rec.old.get('group')
    nv_group = rec.new.get('group')
    if ov_group != nv_group:
        changes.append(f'group {ov_group}→{nv_group}')
    # <category @tags> list diff.
    changes.extend(_list_diff('tags', _category_list(rec.old, 'tags'),
                               _category_list(rec.new, 'tags')))
    # <category @faction> list diff.
    changes.extend(_list_diff('faction', _category_list(rec.old, 'faction'),
                               _category_list(rec.new, 'faction')))

    group_ref = rec.new.get('group')
    refs: dict = {'group_ref': group_ref} if group_ref else {}
    if group_ref and group_ref not in new_sg_names:
        refs['station_group_unresolved'] = True
        extras.warnings.append((
            f'station {station_id}: group_ref {group_ref!r} unresolved',
            {'reason': 'ref_target_unresolved',
             'ref_kind': 'station_group',
             'entity_key': ('station', station_id),
             'affected_keys': [('station', station_id)]},
        ))

    if not changes:
        return None
    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(station_id, classifications, srcs, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('station', station_id),
        'kind': 'modified',
        'subsource': 'station',
        'classifications': classifications,
        'station_id': station_id,
        'refs': refs,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })


def _station_classifications(station: ET.Element) -> list[str]:
    """`['station', ...<category @tags>]` with the generic filter applied only
    to data-derived tokens (tags). The 'station' seed stays as the prefix."""
    out: list[str] = ['station']
    for t in _category_list(station, 'tags'):
        if t in _GENERIC_FILTER or t in out:
            continue
        out.append(t)
    return out


def _category_list(parent: ET.Element, attr: str) -> list[str]:
    """Parse `<category @attr>` as a bracket-or-bare list.

    Shapes observed: `"factory"`, `"[argon, antigone]"`. Both tokenize to a
    clean list without brackets or whitespace. Empty or missing → [].
    """
    category = parent.find('category')
    if category is None:
        return []
    raw = (category.get(attr) or '').strip()
    if not raw:
        return []
    inner = raw.strip('[]')
    return [tok.strip() for tok in inner.split(',') if tok.strip()]


def _list_diff(label: str, old: list[str], new: list[str]) -> list[str]:
    """Return at most one label line per list attribute. Empty if unchanged."""
    if old == new:
        return []
    return [f'{label} {old}→{new}']


# ---------- stationgroup sub-source ----------


def _emit_stationgroup_subsource(
    report, old_plan_ids: set[str], new_plan_ids: set[str],
    extras: _ExtraFailuresReport,
) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_emit_stationgroup_row(
            rec, side='new', plan_ids=new_plan_ids, extras=extras,
        ))
    for rec in report.removed:
        outputs.append(_emit_stationgroup_row(
            rec, side='old', plan_ids=old_plan_ids, extras=extras,
        ))
    for rec in report.modified:
        out = _emit_stationgroup_modified(rec, new_plan_ids, extras)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_stationgroup_row(rec, side: str, plan_ids: set[str],
                            extras: _ExtraFailuresReport) -> RuleOutput:
    group = rec.element
    name = rec.key
    plan_refs = _stationgroup_plan_refs(group)
    _validate_refs(
        extras, name, 'stationgroup', plan_refs, plan_ids, 'plan',
    )
    kind = 'added' if side == 'new' else 'removed'
    if kind == 'added':
        srcs = render_sources(None, rec.sources)
        parts = ['NEW']
    else:
        srcs = render_sources(rec.sources, None)
        parts = ['REMOVED']
    total_entry_count = len(group.findall('select'))
    if total_entry_count:
        parts.append(f'total_entry_count={total_entry_count}')
    classifications = ['stationgroup']
    text = _format(name, classifications, srcs, parts)
    out_extras = {
        'entity_key': ('stationgroup', name),
        'kind': kind,
        'subsource': 'stationgroup',
        'classifications': classifications,
        'group_name': name,
        'refs': {'plan_refs': list(plan_refs)},
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(rec.sources)
    else:
        out_extras['old_sources'] = list(rec.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_stationgroup_modified(rec, new_plan_ids: set[str],
                                 extras: _ExtraFailuresReport
                                 ) -> Optional[RuleOutput]:
    name = rec.key
    old_selects = _selects_by_key(rec.old, 'constructionplan')
    new_selects = _selects_by_key(rec.new, 'constructionplan')
    changes = _select_entries_diff(old_selects, new_selects, '@constructionplan')
    old_count = len(rec.old.findall('select'))
    new_count = len(rec.new.findall('select'))
    if old_count != new_count:
        changes.append(f'total_entry_count {old_count}→{new_count}')

    plan_refs = _stationgroup_plan_refs(rec.new)
    _validate_refs(
        extras, name, 'stationgroup', plan_refs, new_plan_ids, 'plan',
    )
    if not changes:
        return None
    srcs = render_sources(rec.old_sources, rec.new_sources)
    classifications = ['stationgroup']
    text = _format(name, classifications, srcs, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('stationgroup', name),
        'kind': 'modified',
        'subsource': 'stationgroup',
        'classifications': classifications,
        'group_name': name,
        'refs': {'plan_refs': list(plan_refs)},
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })


def _stationgroup_plan_refs(group: ET.Element) -> list[str]:
    return [s.get('constructionplan') for s in group.findall('select')
            if s.get('constructionplan')]


def _selects_by_key(parent: ET.Element, key_attr: str) -> dict[str, ET.Element]:
    """Index `<select>` children by the given attribute. Missing keys skipped.

    If the same key appears twice the last one wins; this is a rare corner in
    real data (not present in 9.00B6), but we diff by attribute key so
    ordering changes alone don't emit rows.
    """
    out: dict[str, ET.Element] = {}
    for s in parent.findall('select'):
        k = s.get(key_attr)
        if k is not None:
            out[k] = s
    return out


def _select_entries_diff(old: dict[str, ET.Element], new: dict[str, ET.Element],
                          key_label: str) -> list[str]:
    """Diff `<select>` entries keyed by one attribute; compare `@chance`.

    Emits `select[<key>=V] added`, `removed`, or `chance OV→NV` lines.
    """
    out: list[str] = []
    for k in sorted(new.keys() - old.keys()):
        chance = new[k].get('chance')
        suffix = f' chance={chance}' if chance is not None else ''
        out.append(f'select[{key_label}={k}] added{suffix}')
    for k in sorted(old.keys() - new.keys()):
        out.append(f'select[{key_label}={k}] removed')
    for k in sorted(old.keys() & new.keys()):
        ov = old[k].get('chance')
        nv = new[k].get('chance')
        if ov != nv:
            out.append(f'select[{key_label}={k}] chance {ov}→{nv}')
    return out


# ---------- module sub-source ----------


def _emit_module_subsource(report, loc_old: Locale, loc_new: Locale,
                            extras: _ExtraFailuresReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_emit_module_row(rec, side='new', loc=loc_new))
    for rec in report.removed:
        outputs.append(_emit_module_row(rec, side='old', loc=loc_old))
    for rec in report.modified:
        out = _emit_module_modified(rec, loc_new)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_module_row(rec, side: str, loc: Locale) -> RuleOutput:
    module = rec.element
    module_id = rec.key
    name = _module_display_name(module, loc, module_id)
    classifications = _module_classifications(module)
    refs = _module_refs(module)
    kind = 'added' if side == 'new' else 'removed'
    if kind == 'added':
        srcs = render_sources(None, rec.sources)
        parts = ['NEW']
    else:
        srcs = render_sources(rec.sources, None)
        parts = ['REMOVED']
    text = _format(name, classifications, srcs, parts)
    out_extras = {
        'entity_key': ('module', module_id),
        'kind': kind,
        'subsource': 'module',
        'classifications': classifications,
        'module_id': module_id,
        'refs': refs,
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(rec.sources)
    else:
        out_extras['old_sources'] = list(rec.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_module_modified(rec, loc_new: Locale) -> Optional[RuleOutput]:
    module_id = rec.key
    name = _module_display_name(rec.new, loc_new, module_id)
    classifications = _module_classifications(rec.new)
    changes: list[str] = []
    ov_class = rec.old.get('class')
    nv_class = rec.new.get('class')
    if ov_class != nv_class:
        changes.append(f'class {ov_class}→{nv_class}')

    for attr in ('ware', 'tags', 'faction', 'race'):
        ov = _category_list(rec.old, attr)
        nv = _category_list(rec.new, attr)
        if attr == 'ware':
            # ware is a single value, not a list; still keep diff form.
            ov_str = ov[0] if ov else None
            nv_str = nv[0] if nv else None
            if ov_str != nv_str:
                changes.append(f'category.ware {ov_str}→{nv_str}')
        else:
            changes.extend(_list_diff(f'category.{attr}', ov, nv))
    changes.extend(_compatibilities_diff(rec.old, rec.new))
    changes.extend(_module_production_diff(rec.old, rec.new))

    if not changes:
        return None
    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, srcs, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('module', module_id),
        'kind': 'modified',
        'subsource': 'module',
        'classifications': classifications,
        'module_id': module_id,
        'refs': _module_refs(rec.new),
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })


def _module_display_name(module: ET.Element, loc: Locale,
                         fallback: str) -> str:
    ident = module.find('identification')
    if ident is None:
        return fallback
    return resolve_attr_ref(ident, loc, attr='name', fallback=fallback)


def _module_classifications(module: ET.Element) -> list[str]:
    """`['module', @class, ...<category @tags>, ...<category @faction>,
    ...<category @race>]`.

    Generic filter applies only to data-derived tokens; the 'module' seed
    stays as the prefix. `_GENERIC_FILTER` also de-dups the seed against any
    redundant 'module' token that appears in <category @tags> in real data.
    """
    out: list[str] = ['module']
    data_tokens: list[str] = []
    cls = module.get('class')
    if cls:
        data_tokens.append(cls)
    for attr in ('tags', 'faction', 'race'):
        data_tokens.extend(_category_list(module, attr))
    seen: set[str] = set(out)
    for t in data_tokens:
        if t in _GENERIC_FILTER or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _module_refs(module: ET.Element) -> dict:
    out: dict = {}
    category = module.find('category')
    if category is not None:
        ware = category.get('ware')
        if ware:
            out['ware_produced'] = ware
    return out


def _compatibilities_diff(old_module: ET.Element,
                          new_module: ET.Element) -> list[str]:
    """Diff `<compatibilities><limits>` and `<maxlimits>` attributes."""
    out: list[str] = []
    for tag in ('limits', 'maxlimits'):
        ov = old_module.find(f'compatibilities/{tag}')
        nv = new_module.find(f'compatibilities/{tag}')
        ov_attrs = dict(ov.attrib) if ov is not None else {}
        nv_attrs = dict(nv.attrib) if nv is not None else {}
        for k in sorted(set(ov_attrs) | set(nv_attrs)):
            if ov_attrs.get(k) != nv_attrs.get(k):
                out.append(
                    f'compatibilities.{tag}.{k} '
                    f'{ov_attrs.get(k)}→{nv_attrs.get(k)}',
                )
    return out


def _module_production_diff(old_module: ET.Element,
                             new_module: ET.Element) -> list[str]:
    """Diff `<production>` entries under `<compatibilities>` keyed by @ware.

    Entries live under `<compatibilities>` per real-data shape. Diff `@chance`.
    """
    old_prod = {p.get('ware'): p
                for p in old_module.findall('compatibilities/production')
                if p.get('ware')}
    new_prod = {p.get('ware'): p
                for p in new_module.findall('compatibilities/production')
                if p.get('ware')}
    out: list[str] = []
    for w in sorted(new_prod.keys() - old_prod.keys()):
        ch = new_prod[w].get('chance')
        suffix = f' chance={ch}' if ch is not None else ''
        out.append(f'production[ware={w}] added{suffix}')
    for w in sorted(old_prod.keys() - new_prod.keys()):
        out.append(f'production[ware={w}] removed')
    for w in sorted(old_prod.keys() & new_prod.keys()):
        ov = old_prod[w].get('chance')
        nv = new_prod[w].get('chance')
        if ov != nv:
            out.append(f'production[ware={w}] chance {ov}→{nv}')
    return out


# ---------- modulegroup sub-source ----------


def _emit_modulegroup_subsource(
    report, old_module_ids: set[str], new_module_ids: set[str],
    extras: _ExtraFailuresReport,
) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_emit_modulegroup_row(
            rec, side='new', module_ids=new_module_ids, extras=extras,
        ))
    for rec in report.removed:
        outputs.append(_emit_modulegroup_row(
            rec, side='old', module_ids=old_module_ids, extras=extras,
        ))
    for rec in report.modified:
        out = _emit_modulegroup_modified(rec, new_module_ids, extras)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_modulegroup_row(rec, side: str, module_ids: set[str],
                          extras: _ExtraFailuresReport) -> RuleOutput:
    group = rec.element
    name = rec.key
    macro_refs = _modulegroup_macro_refs(group)
    # Real X4 data: `select @macro` values reference on-disk macro files
    # (with `_macro` suffix) that are NOT defined in modules.xml — 178/178
    # entries in 9.00B6 fail a literal `@id` match. Rather than spam 178
    # warnings, classify per-entry as resolved/unresolved into a bucket on
    # extras.refs and emit a single aggregate warning only when unresolved
    # refs exist. The bucket is the primary surface; consumers inspect it.
    unresolved = [r for r in macro_refs if r not in module_ids]
    if unresolved:
        extras.warnings.append((
            f'modulegroup {name}: {len(unresolved)} unresolved module '
            f'macro_ref(s)',
            {'reason': 'ref_target_unresolved',
             'ref_kind': 'module_macro_refs',
             'owner_subsource': 'modulegroup',
             'owner_key': name,
             'unresolved_count': len(unresolved),
             'unresolved_refs': unresolved,
             'affected_keys': [('modulegroup', name)]},
        ))
    kind = 'added' if side == 'new' else 'removed'
    if kind == 'added':
        srcs = render_sources(None, rec.sources)
        parts = ['NEW']
    else:
        srcs = render_sources(rec.sources, None)
        parts = ['REMOVED']
    total_entry_count = len(group.findall('select'))
    if total_entry_count:
        parts.append(f'total_entry_count={total_entry_count}')
    classifications = ['modulegroup']
    text = _format(name, classifications, srcs, parts)
    out_extras = {
        'entity_key': ('modulegroup', name),
        'kind': kind,
        'subsource': 'modulegroup',
        'classifications': classifications,
        'group_name': name,
        'refs': {'module_macro_refs': list(macro_refs)},
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(rec.sources)
    else:
        out_extras['old_sources'] = list(rec.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_modulegroup_modified(rec, new_module_ids: set[str],
                                extras: _ExtraFailuresReport
                                ) -> Optional[RuleOutput]:
    name = rec.key
    old_selects = _selects_by_key(rec.old, 'macro')
    new_selects = _selects_by_key(rec.new, 'macro')
    changes = _select_entries_diff(old_selects, new_selects, '@macro')
    old_count = len(rec.old.findall('select'))
    new_count = len(rec.new.findall('select'))
    if old_count != new_count:
        changes.append(f'total_entry_count {old_count}→{new_count}')

    macro_refs = _modulegroup_macro_refs(rec.new)
    unresolved = [r for r in macro_refs if r not in new_module_ids]
    if unresolved:
        extras.warnings.append((
            f'modulegroup {name}: {len(unresolved)} unresolved module '
            f'macro_ref(s)',
            {'reason': 'ref_target_unresolved',
             'ref_kind': 'module_macro_refs',
             'owner_subsource': 'modulegroup',
             'owner_key': name,
             'unresolved_count': len(unresolved),
             'unresolved_refs': unresolved,
             'affected_keys': [('modulegroup', name)]},
        ))
    if not changes:
        return None
    srcs = render_sources(rec.old_sources, rec.new_sources)
    classifications = ['modulegroup']
    text = _format(name, classifications, srcs, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('modulegroup', name),
        'kind': 'modified',
        'subsource': 'modulegroup',
        'classifications': classifications,
        'group_name': name,
        'refs': {'module_macro_refs': list(macro_refs)},
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })


def _modulegroup_macro_refs(group: ET.Element) -> list[str]:
    return [s.get('macro') for s in group.findall('select')
            if s.get('macro')]


# ---------- constructionplan sub-source ----------


def _emit_constructionplan_subsource(
    report,
    old_module_ids: set[str], new_module_ids: set[str],
    old_modulegroup_names: set[str], new_modulegroup_names: set[str],
    extras: _ExtraFailuresReport,
) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_emit_constructionplan_row(
            rec, side='new',
            module_ids=new_module_ids,
            modulegroup_names=new_modulegroup_names,
            extras=extras,
        ))
    for rec in report.removed:
        outputs.append(_emit_constructionplan_row(
            rec, side='old',
            module_ids=old_module_ids,
            modulegroup_names=old_modulegroup_names,
            extras=extras,
        ))
    for rec in report.modified:
        out = _emit_constructionplan_modified(
            rec, new_module_ids, new_modulegroup_names, extras,
        )
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_constructionplan_row(rec, side: str, module_ids: set[str],
                                modulegroup_names: set[str],
                                extras: _ExtraFailuresReport) -> RuleOutput:
    plan = rec.element
    plan_id = rec.key
    refs, has_collision = _classify_plan_refs(
        plan, plan_id, module_ids, modulegroup_names, extras,
    )
    kind = 'added' if side == 'new' else 'removed'
    if kind == 'added':
        srcs = render_sources(None, rec.sources)
        parts = ['NEW']
    else:
        srcs = render_sources(rec.sources, None)
        parts = ['REMOVED']
    race = plan.get('race')
    if race:
        parts.append(f'race={race}')
    total_entry_count = len(plan.findall('entry'))
    if total_entry_count:
        parts.append(f'total_entry_count={total_entry_count}')
    classifications = ['constructionplan']
    text = _format(plan_id, classifications, srcs, parts)
    out_extras = {
        'entity_key': ('constructionplan', plan_id),
        'kind': kind,
        'subsource': 'constructionplan',
        'classifications': classifications,
        'plan_id': plan_id,
        'refs': refs,
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    }
    if has_collision:
        out_extras['incomplete'] = True
    if kind == 'added':
        out_extras['new_sources'] = list(rec.sources)
    else:
        out_extras['old_sources'] = list(rec.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_constructionplan_modified(rec, new_module_ids: set[str],
                                     new_modulegroup_names: set[str],
                                     extras: _ExtraFailuresReport
                                     ) -> Optional[RuleOutput]:
    plan_id = rec.key
    # Diff top-level attrs + entries.
    changes: list[str] = []
    ov_race = rec.old.get('race')
    nv_race = rec.new.get('race')
    if ov_race != nv_race:
        changes.append(f'race {ov_race}→{nv_race}')
    old_entries = _plan_entries(rec.old)
    new_entries = _plan_entries(rec.new)
    changes.extend(_plan_entries_diff(old_entries, new_entries))
    old_count = len(rec.old.findall('entry'))
    new_count = len(rec.new.findall('entry'))
    if old_count != new_count:
        changes.append(f'total_entry_count {old_count}→{new_count}')

    refs, has_collision = _classify_plan_refs(
        rec.new, plan_id, new_module_ids, new_modulegroup_names, extras,
    )
    if not changes:
        return None
    srcs = render_sources(rec.old_sources, rec.new_sources)
    classifications = ['constructionplan']
    text = _format(plan_id, classifications, srcs, changes)
    extras_out = {
        'entity_key': ('constructionplan', plan_id),
        'kind': 'modified',
        'subsource': 'constructionplan',
        'classifications': classifications,
        'plan_id': plan_id,
        'refs': refs,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    }
    if has_collision:
        extras_out['incomplete'] = True
    return RuleOutput(tag=TAG, text=text, extras=extras_out)


def _plan_entries(plan: ET.Element) -> dict[tuple[str, str], ET.Element]:
    """Index `<entry>` children by `(@macro, @index)`. Missing keys skipped."""
    out: dict[tuple[str, str], ET.Element] = {}
    for e in plan.findall('entry'):
        macro = e.get('macro')
        idx = e.get('index')
        if macro is None or idx is None:
            continue
        out[(macro, idx)] = e
    return out


def _plan_entries_diff(old: dict[tuple[str, str], ET.Element],
                        new: dict[tuple[str, str], ET.Element]) -> list[str]:
    """Diff plan entries keyed by (@macro, @index); compare `@connection`."""
    out: list[str] = []
    for k in sorted(new.keys() - old.keys()):
        macro, idx = k
        out.append(f'entry[macro={macro},index={idx}] added')
    for k in sorted(old.keys() - new.keys()):
        macro, idx = k
        out.append(f'entry[macro={macro},index={idx}] removed')
    for k in sorted(old.keys() & new.keys()):
        ov = old[k].get('connection')
        nv = new[k].get('connection')
        if ov != nv:
            macro, idx = k
            out.append(
                f'entry[macro={macro},index={idx}] connection {ov}→{nv}')
    return out


def _classify_plan_refs(plan: ET.Element, plan_id: str,
                         module_ids: set[str], modulegroup_names: set[str],
                         extras: _ExtraFailuresReport
                         ) -> tuple[dict, bool]:
    """Split `<entry @macro>` values into module_refs / modulegroup_refs /
    unresolved, and detect namespace collisions.

    Returns `(refs_dict, has_namespace_collision)`.
    """
    entry_module_refs: list[str] = []
    entry_modulegroup_refs: list[str] = []
    entry_unresolved_refs: list[str] = []
    colliding: list[str] = []
    for e in plan.findall('entry'):
        macro_ref = e.get('macro')
        if not macro_ref:
            continue
        in_modules = macro_ref in module_ids
        in_groups = macro_ref in modulegroup_names
        if in_modules and in_groups:
            colliding.append(macro_ref)
            # Still record both typed refs so downstream can inspect — but
            # mark incomplete via extras.failures below.
            entry_module_refs.append(macro_ref)
            entry_modulegroup_refs.append(macro_ref)
            continue
        if in_modules:
            entry_module_refs.append(macro_ref)
        elif in_groups:
            entry_modulegroup_refs.append(macro_ref)
        else:
            entry_unresolved_refs.append(macro_ref)
    if colliding:
        for ref in colliding:
            extras.failures.append((
                f'constructionplan {plan_id}: entry @macro={ref!r} '
                'resolves as both module @id and modulegroup @name',
                {'reason': 'ref_namespace_collision',
                 'plan_id': plan_id,
                 'macro_ref': ref,
                 'affected_keys': [('constructionplan', plan_id)]},
            ))
    # Per-entry unresolved warnings are suppressed. In real X4 data 9.00B6,
    # 3246/3492 plan entries reference on-disk macro files that aren't in
    # modules.xml or modulegroups.xml — firing a warning per entry would
    # produce ~3400 rows of noise. The `entry_unresolved_refs` bucket on the
    # plan row IS the surface; consumers inspect it directly.
    refs = {
        'entry_module_refs': entry_module_refs,
        'entry_modulegroup_refs': entry_modulegroup_refs,
        'entry_unresolved_refs': entry_unresolved_refs,
    }
    return refs, bool(colliding)


# ---------- ref validation (shared) ----------


def _validate_refs(extras: _ExtraFailuresReport, owner_key: str,
                   owner_subsource: str, refs: list[str],
                   target_set: set[str], target_kind: str) -> None:
    """Emit a `ref_target_unresolved` warning for each ref missing from `target_set`."""
    for ref in refs:
        if ref in target_set:
            continue
        extras.warnings.append((
            f'{owner_subsource} {owner_key}: {target_kind}_ref {ref!r} unresolved',
            {'reason': 'ref_target_unresolved',
             'ref_kind': f'{target_kind}_ref',
             'owner_subsource': owner_subsource,
             'owner_key': owner_key,
             'unresolved_ref': ref,
             'affected_keys': [(owner_subsource, owner_key)]},
        ))


# ---------- formatting ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'
