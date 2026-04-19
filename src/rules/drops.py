"""Drops rule: emit outputs for ammo / wares / droplist library changes.

Three sub-sources share the `drops` tag, distinguished by `extras.subsource`:

- `ammo` — `<ammo id="...">` entries in `libraries/drops.xml`. Fields:
  `<select>` entries keyed by `@macro`. Each select contributes @weight,
  @min, @max attribute changes.
- `wares` — `<wares id="...">` entries in `libraries/drops.xml`. Fields:
  `<select>` entries — identity lives in nested `<ware>` children, not on
  the select's own attributes. Multiset-matched by canonical signature
  `(select.@weight, tuple(sorted((ware.@ware, ware.@amount)...)))`. No
  "modified" — old-only sig → removed, new-only sig → added.
- `droplist` — `<droplist id="...">` entries in `libraries/drops.xml`.
  Fields: child `<drop>` entries. `<drop>` has no id; multiset-matched by
  signature that includes drop's own attributes AND its nested ware payload.
  No "modified".

Note: `<drop>` is nested under `<droplist>` and has no `@id` — it is NOT
a top-level entity. It surfaces as a child of the droplist sub-source's
changes.

All three sub-sources route failures through `forward_incomplete_many`
with per-subsource scoping so one kind's patch error cannot contaminate
rows from another kind.
"""
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.rule_output import RuleOutput, format_row, render_sources


TAG = 'drops'

# Generic tokens stripped from classifications.


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit drops rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; the rule is library-driven.
    """
    outputs: list[RuleOutput] = []

    ammo_report = diff_library(
        old_root, new_root, 'libraries/drops.xml', './/ammo',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='drops_ammo',
    )
    wares_report = diff_library(
        old_root, new_root, 'libraries/drops.xml', './/wares',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='drops_wares',
    )
    droplist_report = diff_library(
        old_root, new_root, 'libraries/drops.xml', './/droplist',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='drops_droplist',
    )

    outputs.extend(_emit_ammo(ammo_report))
    outputs.extend(_emit_wares(wares_report))
    outputs.extend(_emit_droplist(droplist_report))

    forward_incomplete_many(
        [
            (_PrefixedReport(ammo_report, 'ammo'), 'ammo'),
            (_PrefixedReport(wares_report, 'wares'), 'wares'),
            (_PrefixedReport(droplist_report, 'droplist'), 'droplist'),
        ],
        outputs, tag=TAG,
    )
    forward_warnings(ammo_report.warnings, outputs, tag=TAG)
    forward_warnings(wares_report.warnings, outputs, tag=TAG)
    forward_warnings(droplist_report.warnings, outputs, tag=TAG)
    return outputs


class _PrefixedReport:
    """Wraps a DiffReport so `forward_incomplete` sees tuple entity_keys.

    Rule outputs use `(subsource, inner_key)` tuple entity_keys; underlying
    DiffReport.failures carry raw ids via `_infer_affected_keys`. This
    wrapper rewrites them to match so contamination scoping works.
    """
    def __init__(self, report, subsource: str):
        self._report = report
        self._subsource = subsource

    @property
    def incomplete(self) -> bool:
        return bool(getattr(self._report, 'incomplete', False))

    @property
    def failures(self) -> list[tuple[str, dict]]:
        out = []
        for text, extras in getattr(self._report, 'failures', []) or []:
            new_extras = dict(extras)
            ak = new_extras.get('affected_keys') or []
            new_extras['affected_keys'] = [(self._subsource, k) for k in ak]
            out.append((text, new_extras))
        return out

    @property
    def warnings(self) -> list[tuple[str, dict]]:
        return list(getattr(self._report, 'warnings', []) or [])


def _emit_ammo(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_emit_ammo_added(record))
    for record in report.removed:
        outputs.append(_emit_ammo_removed(record))
    for record in report.modified:
        out = _emit_ammo_modified(record)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_ammo_added(record) -> RuleOutput:
    ammo = record.element
    aid = record.key
    classifications = _classifications('ammo')
    sources_label = render_sources(None, record.sources)
    text = format_row(TAG, aid, classifications, sources_label, ['NEW'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ammo', aid),
        'kind': 'added',
        'subsource': 'ammo',
        'classifications': classifications,
        'ammo_id': aid,
        'new_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })


def _emit_ammo_removed(record) -> RuleOutput:
    aid = record.key
    classifications = _classifications('ammo')
    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, aid, classifications, sources_label, ['REMOVED'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ammo', aid),
        'kind': 'removed',
        'subsource': 'ammo',
        'classifications': classifications,
        'ammo_id': aid,
        'old_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })


def _emit_ammo_modified(record):
    aid = record.key
    changes = _diff_ammo_selects(record.old, record.new)
    if not changes:
        return None
    classifications = _classifications('ammo')
    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, aid, classifications, sources_label, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('ammo', aid),
        'kind': 'modified',
        'subsource': 'ammo',
        'classifications': classifications,
        'ammo_id': aid,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })


# Attributes diffed per ammo <select>.
_AMMO_SELECT_ATTRS = ('weight', 'min', 'max')


def _diff_ammo_selects(old_ammo: ElementTree.Element, new_ammo: ElementTree.Element) -> list[str]:
    """Diff <select> entries keyed by @macro.

    Each ammo <select> has a @macro attribute that's unique within the
    <ammo> block, so it's a natural key. Add/remove/attribute-change surface
    per-macro labels.
    """
    old_map = _index_children(old_ammo, 'select', lambda e: e.get('macro'))
    new_map = _index_children(new_ammo, 'select', lambda e: e.get('macro'))
    out: list[str] = []
    for macro in sorted(new_map.keys() - old_map.keys()):
        attributes = _fmt_select_attrs(new_map[macro], _AMMO_SELECT_ATTRS)
        out.append(f'select[macro={macro}] added ({attributes})' if attributes
                   else f'select[macro={macro}] added')
    for macro in sorted(old_map.keys() - new_map.keys()):
        attributes = _fmt_select_attrs(old_map[macro], _AMMO_SELECT_ATTRS)
        out.append(f'select[macro={macro}] removed (was {attributes})' if attributes
                   else f'select[macro={macro}] removed')
    for macro in sorted(old_map.keys() & new_map.keys()):
        o_el = old_map[macro]
        n_el = new_map[macro]
        for a in _AMMO_SELECT_ATTRS:
            old_value = o_el.get(a)
            new_value = n_el.get(a)
            if old_value != new_value:
                out.append(f'select[macro={macro}] {a} {old_value}→{new_value}')
    return out


def _emit_wares(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_emit_wares_added(record))
    for record in report.removed:
        outputs.append(_emit_wares_removed(record))
    for record in report.modified:
        out = _emit_wares_modified(record)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_wares_added(record) -> RuleOutput:
    wid = record.key
    classifications = _classifications('wares')
    sources_label = render_sources(None, record.sources)
    text = format_row(TAG, wid, classifications, sources_label, ['NEW'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('wares', wid),
        'kind': 'added',
        'subsource': 'wares',
        'classifications': classifications,
        'wares_id': wid,
        'new_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })


def _emit_wares_removed(record) -> RuleOutput:
    wid = record.key
    classifications = _classifications('wares')
    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, wid, classifications, sources_label, ['REMOVED'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('wares', wid),
        'kind': 'removed',
        'subsource': 'wares',
        'classifications': classifications,
        'wares_id': wid,
        'old_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })


def _emit_wares_modified(record):
    wid = record.key
    changes = _diff_wares_selects(record.old, record.new)
    if not changes:
        return None
    classifications = _classifications('wares')
    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, wid, classifications, sources_label, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('wares', wid),
        'kind': 'modified',
        'subsource': 'wares',
        'classifications': classifications,
        'wares_id': wid,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })


def _wares_select_signature(select: ElementTree.Element) -> tuple:
    """Multiset signature for a wares <select>.

    Identity lives in nested <ware> children, not on the select's own
    attributes. Pair key = (select.@weight, sorted((ware.@ware, ware.@amount)...)).
    """
    return (
        select.get('weight'),
        tuple(sorted(
            (w.get('ware'), w.get('amount'))
            for w in select.findall('ware')
        )),
    )


def _diff_wares_selects(old_wares: ElementTree.Element,
                        new_wares: ElementTree.Element) -> list[str]:
    """Multiset-diff <select> children.

    Old-only sig → removed; new-only sig → added. No "modified" under
    multiset (prevents cascade false positives when a ware shifts within
    a basket).
    """
    old_selects = list(old_wares.findall('select'))
    new_selects = list(new_wares.findall('select'))
    old_sigs = [_wares_select_signature(s) for s in old_selects]
    new_sigs = [_wares_select_signature(s) for s in new_selects]
    return _multiset_select_diff(old_selects, old_sigs, new_selects, new_sigs,
                                 label='select', fmt_fn=_fmt_wares_select)


def _fmt_wares_select(select: ElementTree.Element) -> str:
    """Compact string rendering of a wares <select> for add/remove lines."""
    weight = select.get('weight') or ''
    wares = []
    for w in select.findall('ware'):
        w_name = w.get('ware') or ''
        amount = w.get('amount')
        if amount is not None:
            wares.append(f'{w_name}:{amount}')
        else:
            wares.append(w_name)
    parts = []
    if weight:
        parts.append(f'weight={weight}')
    if wares:
        parts.append('wares=[' + ','.join(sorted(wares)) + ']')
    return ' '.join(parts)


def _emit_droplist(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for record in report.added:
        outputs.append(_emit_droplist_added(record))
    for record in report.removed:
        outputs.append(_emit_droplist_removed(record))
    for record in report.modified:
        out = _emit_droplist_modified(record)
        if out is not None:
            outputs.append(out)
    return outputs


def _emit_droplist_added(record) -> RuleOutput:
    did = record.key
    classifications = _classifications('droplist')
    sources_label = render_sources(None, record.sources)
    text = format_row(TAG, did, classifications, sources_label, ['NEW'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('droplist', did),
        'kind': 'added',
        'subsource': 'droplist',
        'classifications': classifications,
        'droplist_id': did,
        'new_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })


def _emit_droplist_removed(record) -> RuleOutput:
    did = record.key
    classifications = _classifications('droplist')
    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, did, classifications, sources_label, ['REMOVED'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('droplist', did),
        'kind': 'removed',
        'subsource': 'droplist',
        'classifications': classifications,
        'droplist_id': did,
        'old_sources': list(record.sources),
        'sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })


def _emit_droplist_modified(record):
    did = record.key
    changes = _diff_droplist_drops(record.old, record.new)
    if not changes:
        return None
    classifications = _classifications('droplist')
    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, did, classifications, sources_label, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('droplist', did),
        'kind': 'modified',
        'subsource': 'droplist',
        'classifications': classifications,
        'droplist_id': did,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })


def _drop_signature(drop: ElementTree.Element) -> tuple:
    """Multiset signature for a <drop> element.

    Includes drop's own attributes (@chance, @macro, @group, @min, @max) AND
    its nested ware payload. Ignoring the drop's own attributes would collapse
    distinct drops with identical ware payloads into one multiset entry,
    losing changes (e.g., a drop gaining a @macro reference while keeping
    the same ware payload).

    The "ware payload" projection uses
    `drop.findall('ware')` — direct <ware> children only. In X4 real data
    wares live under <collectable>/<wares>/<ware>, so the payload tuple is
    usually empty and drops are matched purely on their own attributes.
    """
    return (
        tuple(sorted(drop.attrib.items())),
        tuple(sorted(
            (w.get('ware'), w.get('amount'), w.get('chance'))
            for w in drop.findall('ware')
        )),
    )


def _diff_droplist_drops(old_dl: ElementTree.Element, new_dl: ElementTree.Element) -> list[str]:
    """Multiset-diff <drop> children.

    Old-only sig → removed; new-only sig → added. No "modified".
    """
    old_drops = list(old_dl.findall('drop'))
    new_drops = list(new_dl.findall('drop'))
    old_sigs = [_drop_signature(d) for d in old_drops]
    new_sigs = [_drop_signature(d) for d in new_drops]
    return _multiset_select_diff(old_drops, old_sigs, new_drops, new_sigs,
                                 label='drop', fmt_fn=_fmt_drop)


def _fmt_drop(drop: ElementTree.Element) -> str:
    """Compact string rendering of a <drop> for add/remove lines."""
    parts = []
    for a in sorted(drop.attrib):
        parts.append(f'{a}={drop.get(a)}')
    wares = []
    for w in drop.findall('ware'):
        w_name = w.get('ware') or ''
        amount = w.get('amount')
        chance = w.get('chance')
        bits = [w_name]
        if amount is not None:
            bits.append(f'amount={amount}')
        if chance is not None:
            bits.append(f'chance={chance}')
        wares.append(':'.join(bits))
    out = ' '.join(parts)
    if wares:
        out = (out + ' ' if out else '') + 'wares=[' + ','.join(sorted(wares)) + ']'
    return out


def _multiset_select_diff(old_els, old_sigs, new_els, new_sigs,
                          label: str, fmt_fn) -> list[str]:
    """Bag-diff over two lists of (element, signature) pairs.

    Pairs up min-count matching sigs (no output for those). Emits one
    `<label>[...] added ...` per new-only instance, one
    `<label>[...] removed ...` per old-only instance.

    Output is sorted by rendered text so snapshots are stable regardless
    of source order.
    """
    old_counts: dict[tuple, int] = {}
    new_counts: dict[tuple, int] = {}
    for sig in old_sigs:
        old_counts[sig] = old_counts.get(sig, 0) + 1
    for sig in new_sigs:
        new_counts[sig] = new_counts.get(sig, 0) + 1

    added_parts: list[str] = []
    emitted: dict[tuple, int] = {}
    for sig, element in zip(new_sigs, new_els):
        pairs_used = emitted.get(sig, 0)
        old_has = old_counts.get(sig, 0)
        if pairs_used < old_has:
            emitted[sig] = pairs_used + 1
        else:
            rendered = fmt_fn(element)
            added_parts.append(f'{label} added ({rendered})' if rendered
                               else f'{label} added')

    removed_parts: list[str] = []
    emitted = {}
    for sig, element in zip(old_sigs, old_els):
        pairs_used = emitted.get(sig, 0)
        new_has = new_counts.get(sig, 0)
        if pairs_used < new_has:
            emitted[sig] = pairs_used + 1
        else:
            rendered = fmt_fn(element)
            removed_parts.append(f'{label} removed (was {rendered})'
                                 if rendered else f'{label} removed')

    # Sort within each half for stable output.
    return sorted(added_parts) + sorted(removed_parts)


def _index_children(parent: ElementTree.Element, child_tag: str, key_fn) -> dict:
    """Index direct children of `parent` with tag `child_tag` by `key_fn`.

    Last-wins on collision. Callers should assert uniqueness ahead of
    time if it matters; for drops-ammo `<select @macro>`, duplicates have
    not been observed in real data.
    """
    out: dict = {}
    if parent is None:
        return out
    for element in parent.findall(child_tag):
        k = key_fn(element)
        if k is None:
            continue
        out[k] = element
    return out


def _fmt_select_attrs(select: ElementTree.Element, attributes: tuple) -> str:
    parts = []
    for a in attributes:
        v = select.get(a)
        if v is not None:
            parts.append(f'{a}={v}')
    return ' '.join(parts)


def _classifications(subsource: str) -> list[str]:
    """Return [<subsource>] minus the generic filter.

    Classifications = [subsource] where subsource is
    'ammo', 'wares', or 'droplist'. The generic filter is empty so all
    three survive.
    """
    return [t for t in [subsource] if t]


