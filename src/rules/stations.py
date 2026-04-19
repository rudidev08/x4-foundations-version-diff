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
- modulegroup.module_macro_refs → on-disk macro filename stem under
  `assets/structures/**/*_macro.xml` (via `<select @macro>`).
- constructionplan.entry_macro_refs → on-disk macro filename stem (via
  `<entry @macro>`). Real X4 data: plan entries and modulegroup selects both
  reference on-disk macro files directly, NOT module library ids or
  modulegroup names. Verified 100% match across all 166 modulegroup selects
  and 138 plan entries in 9.00B6.

Unresolved refs surface as `forward_warnings` with reason `'ref_target_unresolved'`.

Validation indices come from the public `DiffReport.effective_{old,new}_root`
surface — never the private `_materialize`.

See `src/rules/stations.md` for the stability contract.
"""
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.rule_output import RuleOutput, format_row, render_sources


TAG = 'stations'

# Generic tokens stripped from classifications so they stay meaningful.
_GENERIC_FILTER = frozenset({'station', 'module', 'stationgroup', 'modulegroup',
                             'constructionplan'})


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


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit stations rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused — rule drives itself off
    five `diff_library` calls.
    """
    outputs: list[RuleOutput] = []
    locale_old, locale_new = Locale.build_pair(old_root, new_root, outputs, tag=TAG)

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
    # On-disk macro stems: what modulegroup `<select @macro>` and plan
    # `<entry @macro>` actually reference in real X4 data.
    new_macro_stems = _on_disk_macro_stems(new_root)
    old_macro_stems = _on_disk_macro_stems(old_root)

    # Rule-synthesized extra failures/warnings, routed per sub-source.
    extras_reports = {
        'station': _ExtraFailuresReport(),
        'stationgroup': _ExtraFailuresReport(),
        'module': _ExtraFailuresReport(),
        'modulegroup': _ExtraFailuresReport(),
        'constructionplan': _ExtraFailuresReport(),
    }

    outputs.extend(_emit_station_subsource(
        station_report, locale_old, locale_new,
        old_stationgroup_names, new_stationgroup_names,
        extras_reports['station'],
    ))
    outputs.extend(_emit_stationgroup_subsource(
        sg_report, old_plan_ids, new_plan_ids,
        extras_reports['stationgroup'],
    ))
    outputs.extend(_emit_module_subsource(
        mod_report, locale_old, locale_new,
        extras_reports['module'],
    ))
    outputs.extend(_emit_modulegroup_subsource(
        mg_report, old_macro_stems, new_macro_stems,
        extras_reports['modulegroup'],
    ))
    outputs.extend(_emit_constructionplan_subsource(
        plan_report,
        old_macro_stems, new_macro_stems,
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


def _on_disk_macro_stems(tree_root: Path) -> set[str]:
    """Return the set of `*_macro.xml` filename stems under `assets/structures/`
    (core + all DLC extensions). Modulegroup `<select @macro>` and
    constructionplan `<entry @macro>` reference these stems directly.
    """
    stems: set[str] = set()
    for p in tree_root.rglob('assets/structures/**/*_macro.xml'):
        stems.add(p.stem)
    ext_dir = tree_root / 'extensions'
    if ext_dir.is_dir():
        for p in ext_dir.rglob('assets/structures/**/*_macro.xml'):
            stems.add(p.stem)
    return stems


def _collect_ids(root: Optional[ElementTree.Element], tag: str, attribute: str) -> set[str]:
    """Collect the set of `@attribute` values on every `<tag>` under root.

    `None` or empty values are skipped.
    """
    if root is None:
        return set()
    out: set[str] = set()
    for element in root.iter(tag):
        v = element.get(attribute)
        if v:
            out.add(v)
    return out


def _emit_station_subsource(report, locale_old: Locale, locale_new: Locale,
                             old_sg_names: set[str], new_sg_names: set[str],
                             extras: _ExtraFailuresReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_emit_station_row(
            record, side='new', locale=locale_new,
            sg_names=new_sg_names, extras=extras,
        ))
    for record in report.removed:
        outputs.append(_emit_station_row(
            record, side='old', locale=locale_old,
            sg_names=old_sg_names, extras=extras,
        ))
    for record in report.modified:
        out = _emit_station_modified(record, locale_new, new_sg_names, extras)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_station_row(record, side: str, locale: Locale,
                      sg_names: set[str],
                      extras: _ExtraFailuresReport) -> RuleOutput:
    """Added/removed station row."""
    station = record.element
    station_id = record.key
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
        sources_label = render_sources(None, record.sources)
        parts = ['NEW']
    else:
        sources_label = render_sources(record.sources, None)
        parts = ['REMOVED']
    text = format_row(TAG, station_id, classifications, sources_label, parts)
    entity_key = ('station', station_id)
    out_extras = {
        'entity_key': entity_key,
        'kind': kind,
        'subsource': 'station',
        'classifications': classifications,
        'station_id': station_id,
        'refs': refs,
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(record.sources)
    else:
        out_extras['old_sources'] = list(record.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_station_modified(record, locale_new: Locale,
                            new_sg_names: set[str],
                            extras: _ExtraFailuresReport) -> Optional[RuleOutput]:
    station_id = record.key
    classifications = _station_classifications(record.new)
    changes: list[str] = []
    # @group diff.
    old_group = record.old.get('group')
    new_group = record.new.get('group')
    if old_group != new_group:
        changes.append(f'group {old_group}→{new_group}')
    # <category @tags> list diff.
    changes.extend(_list_diff('tags', _category_list(record.old, 'tags'),
                               _category_list(record.new, 'tags')))
    # <category @faction> list diff.
    changes.extend(_list_diff('faction', _category_list(record.old, 'faction'),
                               _category_list(record.new, 'faction')))

    group_ref = record.new.get('group')
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
    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, station_id, classifications, sources_label, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('station', station_id),
        'kind': 'modified',
        'subsource': 'station',
        'classifications': classifications,
        'station_id': station_id,
        'refs': refs,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })


def _station_classifications(station: ElementTree.Element) -> list[str]:
    """`['station', ...<category @tags>]` with the generic filter applied only
    to data-derived tokens (tags). The 'station' seed stays as the prefix."""
    out: list[str] = ['station']
    for t in _category_list(station, 'tags'):
        if t in _GENERIC_FILTER or t in out:
            continue
        out.append(t)
    return out


def _category_list(parent: ElementTree.Element, attribute: str) -> list[str]:
    """Parse `<category @attribute>` as a bracket-or-bare list.

    Shapes observed: `"factory"`, `"[argon, antigone]"`. Both tokenize to a
    clean list without brackets or whitespace. Empty or missing → [].
    """
    category = parent.find('category')
    if category is None:
        return []
    raw = (category.get(attribute) or '').strip()
    if not raw:
        return []
    inner = raw.strip('[]')
    return [tok.strip() for tok in inner.split(',') if tok.strip()]


def _list_diff(label: str, old: list[str], new: list[str]) -> list[str]:
    """Return at most one label line per list attribute. Empty if unchanged."""
    if old == new:
        return []
    return [f'{label} {old}→{new}']


def _emit_stationgroup_subsource(
    report, old_plan_ids: set[str], new_plan_ids: set[str],
    extras: _ExtraFailuresReport,
) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_emit_stationgroup_row(
            record, side='new', plan_ids=new_plan_ids, extras=extras,
        ))
    for record in report.removed:
        outputs.append(_emit_stationgroup_row(
            record, side='old', plan_ids=old_plan_ids, extras=extras,
        ))
    for record in report.modified:
        out = _emit_stationgroup_modified(record, new_plan_ids, extras)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_stationgroup_row(record, side: str, plan_ids: set[str],
                            extras: _ExtraFailuresReport) -> RuleOutput:
    group = record.element
    name = record.key
    plan_refs = _stationgroup_plan_refs(group)
    _validate_refs(
        extras, name, 'stationgroup', plan_refs, plan_ids, 'plan',
    )
    kind = 'added' if side == 'new' else 'removed'
    if kind == 'added':
        sources_label = render_sources(None, record.sources)
        parts = ['NEW']
    else:
        sources_label = render_sources(record.sources, None)
        parts = ['REMOVED']
    total_entry_count = len(group.findall('select'))
    if total_entry_count:
        parts.append(f'total_entry_count={total_entry_count}')
    classifications = ['stationgroup']
    text = format_row(TAG, name, classifications, sources_label, parts)
    out_extras = {
        'entity_key': ('stationgroup', name),
        'kind': kind,
        'subsource': 'stationgroup',
        'classifications': classifications,
        'group_name': name,
        'refs': {'plan_refs': list(plan_refs)},
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(record.sources)
    else:
        out_extras['old_sources'] = list(record.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_stationgroup_modified(record, new_plan_ids: set[str],
                                 extras: _ExtraFailuresReport
                                 ) -> Optional[RuleOutput]:
    name = record.key
    old_selects = _selects_by_key(record.old, 'constructionplan')
    new_selects = _selects_by_key(record.new, 'constructionplan')
    changes = _select_entries_diff(old_selects, new_selects, '@constructionplan')
    old_count = len(record.old.findall('select'))
    new_count = len(record.new.findall('select'))
    if old_count != new_count:
        changes.append(f'total_entry_count {old_count}→{new_count}')

    plan_refs = _stationgroup_plan_refs(record.new)
    _validate_refs(
        extras, name, 'stationgroup', plan_refs, new_plan_ids, 'plan',
    )
    if not changes:
        return None
    sources_label = render_sources(record.old_sources, record.new_sources)
    classifications = ['stationgroup']
    text = format_row(TAG, name, classifications, sources_label, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('stationgroup', name),
        'kind': 'modified',
        'subsource': 'stationgroup',
        'classifications': classifications,
        'group_name': name,
        'refs': {'plan_refs': list(plan_refs)},
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })


def _stationgroup_plan_refs(group: ElementTree.Element) -> list[str]:
    return [s.get('constructionplan') for s in group.findall('select')
            if s.get('constructionplan')]


def _selects_by_key(parent: ElementTree.Element, key_attr: str) -> dict[str, ElementTree.Element]:
    """Index `<select>` children by the given attribute. Missing keys skipped.

    If the same key appears twice the last one wins; this is a rare corner in
    real data (not present in 9.00B6), but we diff by attribute key so
    ordering changes alone don't emit rows.
    """
    out: dict[str, ElementTree.Element] = {}
    for s in parent.findall('select'):
        k = s.get(key_attr)
        if k is not None:
            out[k] = s
    return out


def _select_entries_diff(old: dict[str, ElementTree.Element], new: dict[str, ElementTree.Element],
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
        old_value = old[k].get('chance')
        new_value = new[k].get('chance')
        if old_value != new_value:
            out.append(f'select[{key_label}={k}] chance {old_value}→{new_value}')
    return out


def _emit_module_subsource(report, locale_old: Locale, locale_new: Locale,
                            extras: _ExtraFailuresReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_emit_module_row(record, side='new', locale=locale_new))
    for record in report.removed:
        outputs.append(_emit_module_row(record, side='old', locale=locale_old))
    for record in report.modified:
        out = _emit_module_modified(record, locale_new)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_module_row(record, side: str, locale: Locale) -> RuleOutput:
    module = record.element
    module_id = record.key
    name = _module_display_name(module, locale, module_id)
    classifications = _module_classifications(module)
    refs = _module_refs(module)
    kind = 'added' if side == 'new' else 'removed'
    if kind == 'added':
        sources_label = render_sources(None, record.sources)
        parts = ['NEW']
    else:
        sources_label = render_sources(record.sources, None)
        parts = ['REMOVED']
    text = format_row(TAG, name, classifications, sources_label, parts)
    out_extras = {
        'entity_key': ('module', module_id),
        'kind': kind,
        'subsource': 'module',
        'classifications': classifications,
        'module_id': module_id,
        'refs': refs,
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(record.sources)
    else:
        out_extras['old_sources'] = list(record.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_module_modified(record, locale_new: Locale) -> Optional[RuleOutput]:
    module_id = record.key
    name = _module_display_name(record.new, locale_new, module_id)
    classifications = _module_classifications(record.new)
    changes: list[str] = []
    old_class = record.old.get('class')
    new_class = record.new.get('class')
    if old_class != new_class:
        changes.append(f'class {old_class}→{new_class}')

    for attribute in ('ware', 'tags', 'faction', 'race'):
        old_value = _category_list(record.old, attribute)
        new_value = _category_list(record.new, attribute)
        if attribute == 'ware':
            # ware is a single value, not a list; still keep diff form.
            old_str = old_value[0] if old_value else None
            new_str = new_value[0] if new_value else None
            if old_str != new_str:
                changes.append(f'category.ware {old_str}→{new_str}')
        else:
            changes.extend(_list_diff(f'category.{attribute}', old_value, new_value))
    changes.extend(_compatibilities_diff(record.old, record.new))
    changes.extend(_module_production_diff(record.old, record.new))

    if not changes:
        return None
    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, name, classifications, sources_label, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('module', module_id),
        'kind': 'modified',
        'subsource': 'module',
        'classifications': classifications,
        'module_id': module_id,
        'refs': _module_refs(record.new),
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })


def _module_display_name(module: ElementTree.Element, locale: Locale,
                         fallback: str) -> str:
    ident = module.find('identification')
    if ident is None:
        return fallback
    return resolve_attr_ref(ident, locale, attribute='name', fallback=fallback)


def _module_classifications(module: ElementTree.Element) -> list[str]:
    """`['module', @class, ...<category @tags>, ...<category @faction>,
    ...<category @race>]`.

    Generic filter applies only to data-derived tokens; the 'module' seed
    stays as the prefix. `_GENERIC_FILTER` also de-dups the seed against any
    redundant 'module' token that appears in <category @tags> in real data.
    """
    out: list[str] = ['module']
    data_tokens: list[str] = []
    class_attr = module.get('class')
    if class_attr:
        data_tokens.append(class_attr)
    for attribute in ('tags', 'faction', 'race'):
        data_tokens.extend(_category_list(module, attribute))
    seen: set[str] = set(out)
    for t in data_tokens:
        if t in _GENERIC_FILTER or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _module_refs(module: ElementTree.Element) -> dict:
    out: dict = {}
    category = module.find('category')
    if category is not None:
        ware = category.get('ware')
        if ware:
            out['ware_produced'] = ware
    return out


def _compatibilities_diff(old_module: ElementTree.Element,
                          new_module: ElementTree.Element) -> list[str]:
    """Diff `<compatibilities><limits>` and `<maxlimits>` attributes."""
    out: list[str] = []
    for tag in ('limits', 'maxlimits'):
        old_value = old_module.find(f'compatibilities/{tag}')
        new_value = new_module.find(f'compatibilities/{tag}')
        old_attrs = dict(old_value.attrib) if old_value is not None else {}
        new_attrs = dict(new_value.attrib) if new_value is not None else {}
        for k in sorted(set(old_attrs) | set(new_attrs)):
            if old_attrs.get(k) != new_attrs.get(k):
                out.append(
                    f'compatibilities.{tag}.{k} '
                    f'{old_attrs.get(k)}→{new_attrs.get(k)}',
                )
    return out


def _module_production_diff(old_module: ElementTree.Element,
                             new_module: ElementTree.Element) -> list[str]:
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
        old_value = old_prod[w].get('chance')
        new_value = new_prod[w].get('chance')
        if old_value != new_value:
            out.append(f'production[ware={w}] chance {old_value}→{new_value}')
    return out


def _emit_modulegroup_subsource(
    report, old_macro_stems: set[str], new_macro_stems: set[str],
    extras: _ExtraFailuresReport,
) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_emit_modulegroup_row(
            record, side='new', macro_stems=new_macro_stems, extras=extras,
        ))
    for record in report.removed:
        outputs.append(_emit_modulegroup_row(
            record, side='old', macro_stems=old_macro_stems, extras=extras,
        ))
    for record in report.modified:
        out = _emit_modulegroup_modified(record, new_macro_stems, extras)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_modulegroup_row(record, side: str, macro_stems: set[str],
                          extras: _ExtraFailuresReport) -> RuleOutput:
    group = record.element
    name = record.key
    macro_refs = _modulegroup_macro_refs(group)
    # Check each `<select @macro>` value against on-disk macro filename stems
    # under `assets/structures/**/*_macro.xml`. Real X4: 166/166 resolve. A
    # non-zero unresolved count signals a data anomaly worth surfacing.
    unresolved = [r for r in macro_refs if r not in macro_stems]
    if unresolved:
        extras.warnings.append((
            f'modulegroup {name}: {len(unresolved)} unresolved module '
            f'macro_ref(s) (no matching on-disk *_macro.xml file)',
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
        sources_label = render_sources(None, record.sources)
        parts = ['NEW']
    else:
        sources_label = render_sources(record.sources, None)
        parts = ['REMOVED']
    total_entry_count = len(group.findall('select'))
    if total_entry_count:
        parts.append(f'total_entry_count={total_entry_count}')
    classifications = ['modulegroup']
    text = format_row(TAG, name, classifications, sources_label, parts)
    out_extras = {
        'entity_key': ('modulegroup', name),
        'kind': kind,
        'subsource': 'modulegroup',
        'classifications': classifications,
        'group_name': name,
        'refs': {'module_macro_refs': list(macro_refs)},
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(record.sources)
    else:
        out_extras['old_sources'] = list(record.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_modulegroup_modified(record, new_macro_stems: set[str],
                                extras: _ExtraFailuresReport
                                ) -> Optional[RuleOutput]:
    name = record.key
    old_selects = _selects_by_key(record.old, 'macro')
    new_selects = _selects_by_key(record.new, 'macro')
    changes = _select_entries_diff(old_selects, new_selects, '@macro')
    old_count = len(record.old.findall('select'))
    new_count = len(record.new.findall('select'))
    if old_count != new_count:
        changes.append(f'total_entry_count {old_count}→{new_count}')

    macro_refs = _modulegroup_macro_refs(record.new)
    unresolved = [r for r in macro_refs if r not in new_macro_stems]
    if unresolved:
        extras.warnings.append((
            f'modulegroup {name}: {len(unresolved)} unresolved module '
            f'macro_ref(s) (no matching on-disk *_macro.xml file)',
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
    sources_label = render_sources(record.old_sources, record.new_sources)
    classifications = ['modulegroup']
    text = format_row(TAG, name, classifications, sources_label, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('modulegroup', name),
        'kind': 'modified',
        'subsource': 'modulegroup',
        'classifications': classifications,
        'group_name': name,
        'refs': {'module_macro_refs': list(macro_refs)},
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })


def _modulegroup_macro_refs(group: ElementTree.Element) -> list[str]:
    return [s.get('macro') for s in group.findall('select')
            if s.get('macro')]


def _emit_constructionplan_subsource(
    report,
    old_macro_stems: set[str], new_macro_stems: set[str],
    extras: _ExtraFailuresReport,
) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_emit_constructionplan_row(
            record, side='new', macro_stems=new_macro_stems, extras=extras,
        ))
    for record in report.removed:
        outputs.append(_emit_constructionplan_row(
            record, side='old', macro_stems=old_macro_stems, extras=extras,
        ))
    for record in report.modified:
        out = _emit_constructionplan_modified(record, new_macro_stems, extras)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_constructionplan_row(record, side: str, macro_stems: set[str],
                                extras: _ExtraFailuresReport) -> RuleOutput:
    plan = record.element
    plan_id = record.key
    refs = _classify_plan_refs(plan, plan_id, macro_stems, extras)
    kind = 'added' if side == 'new' else 'removed'
    if kind == 'added':
        sources_label = render_sources(None, record.sources)
        parts = ['NEW']
    else:
        sources_label = render_sources(record.sources, None)
        parts = ['REMOVED']
    race = plan.get('race')
    if race:
        parts.append(f'race={race}')
    total_entry_count = len(plan.findall('entry'))
    if total_entry_count:
        parts.append(f'total_entry_count={total_entry_count}')
    classifications = ['constructionplan']
    text = format_row(TAG, plan_id, classifications, sources_label, parts)
    out_extras = {
        'entity_key': ('constructionplan', plan_id),
        'kind': kind,
        'subsource': 'constructionplan',
        'classifications': classifications,
        'plan_id': plan_id,
        'refs': refs,
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    }
    if kind == 'added':
        out_extras['new_sources'] = list(record.sources)
    else:
        out_extras['old_sources'] = list(record.sources)
    return RuleOutput(tag=TAG, text=text, extras=out_extras)


def _emit_constructionplan_modified(record, new_macro_stems: set[str],
                                     extras: _ExtraFailuresReport
                                     ) -> Optional[RuleOutput]:
    plan_id = record.key
    # Diff top-level attributes + entries.
    changes: list[str] = []
    old_race = record.old.get('race')
    new_race = record.new.get('race')
    if old_race != new_race:
        changes.append(f'race {old_race}→{new_race}')
    old_entries = _plan_entries(record.old)
    new_entries = _plan_entries(record.new)
    changes.extend(_plan_entries_diff(old_entries, new_entries))
    old_count = len(record.old.findall('entry'))
    new_count = len(record.new.findall('entry'))
    if old_count != new_count:
        changes.append(f'total_entry_count {old_count}→{new_count}')

    refs = _classify_plan_refs(record.new, plan_id, new_macro_stems, extras)
    if not changes:
        return None
    sources_label = render_sources(record.old_sources, record.new_sources)
    classifications = ['constructionplan']
    text = format_row(TAG, plan_id, classifications, sources_label, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('constructionplan', plan_id),
        'kind': 'modified',
        'subsource': 'constructionplan',
        'classifications': classifications,
        'plan_id': plan_id,
        'refs': refs,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })


def _plan_entries(plan: ElementTree.Element) -> dict[tuple[str, str], ElementTree.Element]:
    """Index `<entry>` children by `(@macro, @index)`. Missing keys skipped."""
    out: dict[tuple[str, str], ElementTree.Element] = {}
    for e in plan.findall('entry'):
        macro = e.get('macro')
        index = e.get('index')
        if macro is None or index is None:
            continue
        out[(macro, index)] = e
    return out


def _plan_entries_diff(old: dict[tuple[str, str], ElementTree.Element],
                        new: dict[tuple[str, str], ElementTree.Element]) -> list[str]:
    """Diff plan entries keyed by (@macro, @index); compare `@connection`."""
    out: list[str] = []
    for k in sorted(new.keys() - old.keys()):
        macro, index = k
        out.append(f'entry[macro={macro},index={index}] added')
    for k in sorted(old.keys() - new.keys()):
        macro, index = k
        out.append(f'entry[macro={macro},index={index}] removed')
    for k in sorted(old.keys() & new.keys()):
        old_value = old[k].get('connection')
        new_value = new[k].get('connection')
        if old_value != new_value:
            macro, index = k
            out.append(
                f'entry[macro={macro},index={index}] connection {old_value}→{new_value}')
    return out


def _classify_plan_refs(plan: ElementTree.Element, plan_id: str,
                         macro_stems: set[str],
                         extras: _ExtraFailuresReport) -> dict:
    """Split `<entry @macro>` values into resolved (matching an on-disk
    `*_macro.xml` file) and unresolved (no matching file).

    Real X4: all plan entries reference on-disk macro files directly — NOT
    module library ids or modulegroup names. If an entry ref is genuinely
    unresolved against the on-disk set, surface it on `entry_unresolved_refs`
    but don't emit per-entry warnings (plans can have many entries and noise
    would be overwhelming).
    """
    entry_macro_refs: list[str] = []
    entry_unresolved_refs: list[str] = []
    for e in plan.findall('entry'):
        macro_ref = e.get('macro')
        if not macro_ref:
            continue
        entry_macro_refs.append(macro_ref)
        if macro_ref not in macro_stems:
            entry_unresolved_refs.append(macro_ref)
    return {
        'entry_macro_refs': entry_macro_refs,
        'entry_unresolved_refs': entry_unresolved_refs,
    }


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


