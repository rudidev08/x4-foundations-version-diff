"""Cosmetics rule: emit outputs for paintmod / adsign / equipmod changes.

Three USER-FACING sub-sources share the `cosmetics` tag, backed by
multiple internal labels so contamination stays scoped per-file / per-family:

- `paint` (user-facing) — `libraries/paintmods.xml`. One `diff_library` call
  on `.//paint` keyed by `@ware`. Internal label `'paint'`.
- `adsign` (user-facing) — `libraries/adsigns.xml`. TWO sub-reports
  distinguished by internal labels `'adsign_ware'` and `'adsign_waregroup'`
  because real data has BOTH `<adsign ware="...">` and
  `<adsign waregroup="...">`; keying only on `@ware` silently drops all
  `@waregroup` rows (coverage gap). Both map to user-facing `'adsign'`.
  Adsign keys include the enclosing `<type @ref>` since the same ware can
  appear under multiple `<type>` blocks (highway / station / ...). Dual-attr
  rows (both `@ware` and `@waregroup`) are claimed by the `@ware` variant
  and surfaced as a `'adsign_dual_attr'` warning.
- `equipmod` (user-facing) — `libraries/equipmentmods.xml` with **runtime
  family discovery**. Real structure: root contains top-level family tags
  (`<weapon>`, `<shield>`, `<engine>`, `<ship>`, ...) each with leaf mod
  entries. The rule runs one shared `diff_library` materialization call
  to reach the effective old/new trees, iterates direct children of the
  root to discover families, then builds one manual sub-report per family
  with internal label `'equipmod_<family>'`. User-facing classifications
  stay `['equipmod', <family>]`. Leaf mods live as children of `<family>`
  elements with varying tag names (`<damage>`, `<cooling>`, `<speed>`,
  ...) so `diff_library`'s default single-tag indexer can't pick them up
  directly — the rule re-indexes the effective trees per family.

Internal-label stability is a Tier B contract — renaming any of
`paint`, `adsign_ware`, `adsign_waregroup`, `equipmod_<family>` is a
breaking change that forces snapshot regeneration.

See `src/rules/cosmetics.md` for the stability contract + limitations.
"""
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'cosmetics'

# Paint-element attrs diffed directly (HSV + pattern fields). Pulled from
# real 9.00B6 paintmods.xml; any future attr would silently get ignored,
# but the attr set covers the known-used fields.
PAINT_ATTRS = (
    'quality',
    # HSV + material
    'hue', 'brightness', 'saturation', 'metal', 'smooth', 'dirt', 'extradirt',
    # pattern
    'pattern', 'scale', 'strength', 'sharpness', 'invert',
    'red', 'green', 'blue', 'alpha', 'personal',
)

# Adsign leaf-entry attrs diffed directly (besides the key). Real data
# typically has only `@macro`; the keyed attr (ware/waregroup) is listed
# on the row text via `_adsign_attr_variant` so all fields remain
# inspectable.
ADSIGN_ATTRS = ('macro',)

# Equipmod leaf-mod-entry attrs diffed directly (besides ware + quality,
# which are part of the entity key). Real 9.00B6 leaf mods carry `@min`
# and `@max`.
EQUIPMOD_LEAF_ATTRS = ('min', 'max')

# Bonus-level attrs that live on the enclosing <bonus> element.
BONUS_ATTRS = ('chance', 'max', 'value')

# Per-bonus-type (inner child of <bonus>) attrs.
BONUS_INNER_ATTRS = ('min', 'max', 'value', 'weight')


# ---------- synthetic sub-report for bucketed adsign diff ----------


class _ManualDiffReport:
    """Wraps a manually-keyed diff as a DiffReport-shaped surface.

    Two rule-internal callers use this:

    - Adsigns: one base `diff_library` call with `id(e)` keys to reach
      `effective_{old,new}_root`, then re-index via composite key
      `(internal_label, parent_type_ref, value)` so two distinct
      `<type ref=...>` blocks with the same ware don't collide.
    - Equipmods: one base `diff_library` call to reach the effective
      trees, then one manual per-family re-index since leaf mods are
      children of `<family>` elements with varying tag names that
      `_index_by_key` can't enumerate.

    The wrapper presents added/removed/modified lists with the same
    shape `diff_library` returns, plus pass-through of warnings +
    failures from the underlying base report so contamination scoping
    stays exact.
    """
    def __init__(self, added, removed, modified,
                 warnings, failures):
        self.added = added
        self.removed = removed
        self.modified = modified
        self.warnings = warnings
        self.failures = failures

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


class _ManualRec:
    """Added/removed row surface — mirrors EntityRecord shape."""
    def __init__(self, key, element, sources):
        self.key = key
        self.element = element
        self.sources = sources


class _ManualModRec:
    """Modified row surface — mirrors ModifiedRecord shape."""
    def __init__(self, key, old_el, new_el, old_sources, new_sources):
        self.key = key
        self.old = old_el
        self.new = new_el
        self.old_sources = old_sources
        self.new_sources = new_sources


# ---------- top-level entry ----------


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit cosmetics rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; the rule drives itself off
    `diff_library` calls over three files.
    """
    outputs: list[RuleOutput] = []
    reports: list[tuple] = []  # (report, internal_label)

    # 1. Paints.
    paint_report = diff_library(
        old_root, new_root, 'libraries/paintmods.xml', './/paint',
        key_fn=_paint_key, key_fn_identity='cosmetics_paint',
    )
    outputs.extend(_emit_paint(paint_report))
    reports.append((paint_report, 'paint'))

    # 2. Adsigns — one diff_library call to reach effective trees, then
    # manual re-indexing into two buckets (ware / waregroup) keyed by
    # (internal_label, parent_type_ref, value).
    adsign_base = diff_library(
        old_root, new_root, 'libraries/adsigns.xml', './/adsign',
        key_fn=lambda e: id(e),
        key_fn_identity='cosmetics_adsign_identity',
    )
    adsign_ware_report, adsign_wg_report = _build_adsign_reports(adsign_base)
    outputs.extend(_emit_adsign(adsign_ware_report, 'adsign_ware'))
    outputs.extend(_emit_adsign(adsign_wg_report, 'adsign_waregroup'))
    reports.append((adsign_ware_report, 'adsign_ware'))
    reports.append((adsign_wg_report, 'adsign_waregroup'))

    # Dual-attr assertion: any adsign with BOTH @ware and @waregroup.
    dual_attr_warnings = _scan_adsign_dual_attr(adsign_base)
    for w_text, w_extras in dual_attr_warnings:
        outputs.append(RuleOutput(
            tag=TAG, text=f'[{TAG}] WARNING: {w_text}',
            extras={
                'entity_key': _diagnostic_key(w_text),
                'kind': 'warning',
                'subsource': 'diagnostic',
                'classifications': [],
                'warning': True,
                'details': w_extras,
            },
        ))

    # 3. Equipmods — runtime family discovery off the effective trees.
    # We make one discovery call (never-matching xpath) to reach the
    # effective root, then one family-scoped sub-report per family
    # derived from the shared base report. Leaf mods are CHILDREN of
    # `<family>` elements with varying tag names (`<damage>`, `<cooling>`,
    # ...) so we can't let diff_library's `_index_by_key` pick them up
    # directly — it needs a single tag name. Instead, we manually
    # re-index the effective tree per family, same pattern as loadouts.
    equipmod_base = diff_library(
        old_root, new_root, 'libraries/equipmentmods.xml',
        './/__cosmetics_equipmod_base__',
        key_fn=lambda e: None,
        key_fn_identity='cosmetics_equipmod_base',
    )
    families = _discover_equipmod_families_from_report(equipmod_base)
    for family in sorted(families):
        internal = f'equipmod_{family}'
        report = _build_equipmod_report(equipmod_base, family, internal)
        outputs.extend(_emit_equipmod(report, family, internal))
        reports.append((report, internal))

    forward_incomplete_many(reports, outputs, tag=TAG)
    for report, _ in reports:
        forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


# ---------- paint sub-source ----------


def _paint_key(elem: ET.Element) -> Optional[tuple]:
    ware = elem.get('ware')
    if ware is None:
        return None
    return ('paint', ware)


def _emit_paint(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_paint_row(rec, 'added'))
    for rec in report.removed:
        outputs.append(_paint_row(rec, 'removed'))
    for rec in report.modified:
        row = _paint_row(rec, 'modified')
        if row is not None:
            outputs.append(row)
    return outputs


def _paint_row(rec, kind: str) -> Optional[RuleOutput]:
    subsource = 'paint'
    classifications = ['paint']
    if kind == 'added':
        ware = rec.element.get('ware')
        fields = _collect_attrs(rec.element, PAINT_ATTRS)
        parts = ['NEW']
        parts.extend(f'{k}={v}' for k, v in sorted(fields.items()))
        srcs = render_sources(None, rec.sources)
        old_srcs, new_srcs = None, rec.sources
    elif kind == 'removed':
        ware = rec.element.get('ware')
        parts = ['REMOVED']
        srcs = render_sources(rec.sources, None)
        old_srcs, new_srcs = rec.sources, None
    else:  # modified
        ware = rec.new.get('ware')
        old_fields = _collect_attrs(rec.old, PAINT_ATTRS)
        new_fields = _collect_attrs(rec.new, PAINT_ATTRS)
        changes = _diff_attr_map(old_fields, new_fields)
        if not changes:
            return None
        parts = changes
        srcs = render_sources(rec.old_sources, rec.new_sources)
        old_srcs, new_srcs = rec.old_sources, rec.new_sources
    text = _format(ware, classifications, srcs, parts)
    extras = {
        'entity_key': (subsource, ware),
        'kind': kind,
        'subsource': subsource,
        'classifications': classifications,
        'ware': ware,
    }
    if old_srcs is not None:
        extras['old_sources'] = list(old_srcs)
    if new_srcs is not None:
        extras['new_sources'] = list(new_srcs)
    return RuleOutput(tag=TAG, text=text, extras=extras)


# ---------- adsign sub-source ----------


def _build_adsign_reports(base_report):
    """Build two adsign sub-reports (ware / waregroup) from one DiffReport.

    Walks `effective_old_root` / `effective_new_root`, indexes `<adsign>`
    elements under their enclosing `<type @ref>` parents, and pairs them
    by composite key `(internal_label, type_ref, value)`. Dual-attr rows
    (both @ware and @waregroup) are claimed by the `@ware` variant.
    """
    old_tree = base_report.effective_old_root
    new_tree = base_report.effective_new_root

    ware_old = _index_adsigns(old_tree, 'ware')
    ware_new = _index_adsigns(new_tree, 'ware')
    wg_old = _index_adsigns(old_tree, 'waregroup')
    wg_new = _index_adsigns(new_tree, 'waregroup')

    ware_report = _pair_adsign_maps(ware_old, ware_new, 'adsign_ware',
                                     base_report)
    wg_report = _pair_adsign_maps(wg_old, wg_new, 'adsign_waregroup',
                                   base_report)
    return ware_report, wg_report


def _index_adsigns(tree_root: Optional[ET.Element],
                   attr: str) -> dict[tuple, ET.Element]:
    """Index <adsign> elements by (type_ref, attr_value).

    Filters out elements that don't carry `attr`. For the `'waregroup'`
    variant, also drops dual-attr rows (`@ware` wins; the ware variant
    keys them instead). Keys are 2-tuples; the internal-label prefix is
    added by `_pair_adsign_maps`.
    """
    out: dict[tuple, ET.Element] = {}
    if tree_root is None:
        return out
    parent_map = _build_parent_map(tree_root)
    for adsign in tree_root.iter('adsign'):
        if adsign.get(attr) is None:
            continue
        if attr == 'waregroup' and adsign.get('ware') is not None:
            # Dual-attr: claimed by the @ware variant.
            continue
        type_ref = _find_enclosing_type_ref(adsign, parent_map)
        value = adsign.get(attr)
        key = (type_ref, value)
        # Last-wins on collisions within one tree — real data has at
        # most one entry per (type_ref, ware) so collisions are a data
        # issue we don't paper over.
        out[key] = adsign
    return out


def _pair_adsign_maps(old_map: dict[tuple, ET.Element],
                      new_map: dict[tuple, ET.Element],
                      internal_label: str,
                      base_report) -> _ManualDiffReport:
    """Pair two {(type_ref, value): <adsign>} maps into an added/removed/
    modified _ManualDiffReport. Warnings + failures from `base_report` are
    forwarded unchanged so patch failures contaminate both variants'
    outputs (they share the same adsigns.xml file)."""
    added: list[_ManualRec] = []
    removed: list[_ManualRec] = []
    modified: list[_ManualModRec] = []
    for k in sorted(new_map.keys() - old_map.keys(),
                    key=_adsign_key_sort):
        el = new_map[k]
        added.append(_ManualRec(
            (internal_label,) + k, el, _adsign_sources(el, base_report),
        ))
    for k in sorted(old_map.keys() - new_map.keys(),
                    key=_adsign_key_sort):
        el = old_map[k]
        removed.append(_ManualRec(
            (internal_label,) + k, el, _adsign_sources(el, base_report),
        ))
    for k in sorted(old_map.keys() & new_map.keys(),
                    key=_adsign_key_sort):
        old_el = old_map[k]
        new_el = new_map[k]
        if _element_equal(old_el, new_el):
            continue
        modified.append(_ManualModRec(
            (internal_label,) + k, old_el, new_el,
            _adsign_sources(old_el, base_report),
            _adsign_sources(new_el, base_report),
        ))
    return _ManualDiffReport(
        added=added, removed=removed, modified=modified,
        warnings=list(getattr(base_report, 'warnings', []) or []),
        failures=list(getattr(base_report, 'failures', []) or []),
    )


def _adsign_sources(_elem: ET.Element, base_report) -> list[str]:
    """Adsign provenance: `diff_library` attributes contributions to
    entities whose root carries `@id` / `@name`; adsigns have neither.
    We fall back to 'core' as a sensible default so `render_sources`
    produces a non-empty sources column.
    """
    return ['core']


def _adsign_key_sort(k: tuple) -> tuple:
    """Stable sort key — adsign composite is (type_ref|None, value). Coerce
    None to empty string so mixed None/str lists stay sortable."""
    return tuple('' if x is None else str(x) for x in k)


def _build_parent_map(tree_root: ET.Element) -> dict[int, ET.Element]:
    return {id(c): p for p in tree_root.iter() for c in p}


def _find_enclosing_type_ref(elem: ET.Element,
                             parent_map: dict[int, ET.Element]
                             ) -> Optional[str]:
    """Walk up from `elem` to the nearest `<type @ref>` ancestor.

    Returns None if `elem` sits directly under the root with no enclosing
    `<type>` (both test fixtures and real 9.00B6 data always nest
    adsigns under `<type>`, so None is a defensive fallback).
    """
    cur = elem
    seen: set[int] = set()
    while id(cur) in parent_map and id(cur) not in seen:
        seen.add(id(cur))
        parent = parent_map[id(cur)]
        if parent.tag == 'type' and parent.get('ref') is not None:
            return parent.get('ref')
        cur = parent
    return None


def _element_equal(a: ET.Element, b: ET.Element) -> bool:
    """Deep-equality on two elements. Used to skip emitting modified rows
    that are structurally identical (shouldn't happen in practice since
    `diff_library` already filters, but we re-check after manual keying)."""
    if a.tag != b.tag or a.attrib != b.attrib:
        return False
    if (a.text or '').strip() != (b.text or '').strip():
        return False
    if len(a) != len(b):
        return False
    return all(_element_equal(x, y) for x, y in zip(a, b))


def _emit_adsign(report, internal_label: str) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_adsign_row(rec, 'added', internal_label))
    for rec in report.removed:
        outputs.append(_adsign_row(rec, 'removed', internal_label))
    for rec in report.modified:
        row = _adsign_row(rec, 'modified', internal_label)
        if row is not None:
            outputs.append(row)
    return outputs


def _adsign_row(rec, kind: str, internal_label: str) -> Optional[RuleOutput]:
    """Build one adsign row. Internal label scopes the entity_key; the
    user-facing classification is always `'adsign'`."""
    classifications = ['adsign']
    # rec.key is (internal_label, parent_type_ref, value).
    _, parent_type_ref, value = rec.key
    if parent_type_ref:
        display = f'{parent_type_ref}/{value}'
    else:
        display = value or ''

    if kind == 'added':
        fields = _collect_attrs(rec.element, ADSIGN_ATTRS)
        attr_variant = _adsign_attr_variant(internal_label)
        if attr_variant and rec.element.get(attr_variant) is not None:
            fields[attr_variant] = rec.element.get(attr_variant)
        parts = ['NEW']
        parts.extend(f'{k}={v}' for k, v in sorted(fields.items()))
        srcs = render_sources(None, rec.sources)
        old_srcs, new_srcs = None, rec.sources
    elif kind == 'removed':
        parts = ['REMOVED']
        srcs = render_sources(rec.sources, None)
        old_srcs, new_srcs = rec.sources, None
    else:  # modified
        old_fields = _collect_attrs(rec.old, ADSIGN_ATTRS)
        new_fields = _collect_attrs(rec.new, ADSIGN_ATTRS)
        changes = _diff_attr_map(old_fields, new_fields)
        if not changes:
            return None
        parts = changes
        srcs = render_sources(rec.old_sources, rec.new_sources)
        old_srcs, new_srcs = rec.old_sources, rec.new_sources
    text = _format(display, classifications, srcs, parts)
    extras = {
        'entity_key': rec.key,
        'kind': kind,
        'subsource': internal_label,
        'classifications': classifications,
        'parent_type_ref': parent_type_ref,
        'adsign_key': value,
    }
    if old_srcs is not None:
        extras['old_sources'] = list(old_srcs)
    if new_srcs is not None:
        extras['new_sources'] = list(new_srcs)
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _adsign_attr_variant(internal_label: str) -> Optional[str]:
    if internal_label == 'adsign_ware':
        return 'ware'
    if internal_label == 'adsign_waregroup':
        return 'waregroup'
    return None


def _scan_adsign_dual_attr(base_report) -> list[tuple[str, dict]]:
    """Warn about adsigns that carry BOTH `@ware` AND `@waregroup`.

    Scans both effective trees and emits one warning per unique
    (ware, waregroup) pair (de-duplicated across old/new since the same
    malformed row often appears in both). Row still emits under the
    `@ware` key (see `_index_adsigns`).
    """
    out: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for root in (base_report.effective_old_root,
                 base_report.effective_new_root):
        if root is None:
            continue
        for adsign in root.iter('adsign'):
            if adsign.get('ware') is None:
                continue
            if adsign.get('waregroup') is None:
                continue
            ware = adsign.get('ware')
            waregroup = adsign.get('waregroup')
            dedup = f'{ware}|{waregroup}'
            if dedup in seen:
                continue
            seen.add(dedup)
            out.append((
                f'adsign @ware={ware} AND @waregroup={waregroup} — ware wins',
                {
                    'reason': 'adsign_dual_attr',
                    'ware': ware,
                    'waregroup': waregroup,
                },
            ))
    return out


# ---------- equipmod sub-source ----------


def _discover_equipmod_families_from_report(base_report) -> list[str]:
    """Discover equipmod family tags from a materialized base report.

    Iterates direct children of `libraries/equipmentmods.xml`'s root in
    both effective trees and returns the union of tag names. Alphabetical
    order at the caller site gives snapshot stability.
    """
    families: set[str] = set()
    for tree_root in (base_report.effective_old_root,
                      base_report.effective_new_root):
        if tree_root is None:
            continue
        for child in tree_root:
            if child.tag and not child.tag.startswith('{'):
                families.add(child.tag)
    return sorted(families)


def _build_equipmod_report(base_report, family: str,
                           internal_label: str) -> _ManualDiffReport:
    """Build a per-family equipmod sub-report by manually indexing the
    effective trees. Leaf mods live as children of `<family>` elements
    with varying tag names (`<damage>`, `<cooling>`, ...); diff_library's
    default indexer needs a single tag name, so we re-index here.

    Each leaf is keyed by `(internal_label, family, ware, quality)`.
    Leaves without `@ware` are skipped (data bug). Warnings + failures
    from `base_report` pass through unchanged — they describe the whole
    equipmentmods.xml file.

    Returns a _ManualDiffReport shape (added/removed/modified + warnings
    + failures + incomplete property) — the name is generic enough to
    reuse for any manual-keying diff.
    """
    old_map = _index_equipmod_family(base_report.effective_old_root, family,
                                     internal_label)
    new_map = _index_equipmod_family(base_report.effective_new_root, family,
                                     internal_label)
    added: list[_ManualRec] = []
    removed: list[_ManualRec] = []
    modified: list[_ManualModRec] = []
    for k in sorted(new_map.keys() - old_map.keys(),
                    key=_equipmod_key_sort):
        el = new_map[k]
        added.append(_ManualRec(k, el, ['core']))
    for k in sorted(old_map.keys() - new_map.keys(),
                    key=_equipmod_key_sort):
        el = old_map[k]
        removed.append(_ManualRec(k, el, ['core']))
    for k in sorted(old_map.keys() & new_map.keys(),
                    key=_equipmod_key_sort):
        old_el = old_map[k]
        new_el = new_map[k]
        if _element_equal(old_el, new_el):
            continue
        modified.append(_ManualModRec(
            k, old_el, new_el, ['core'], ['core'],
        ))
    return _ManualDiffReport(
        added=added, removed=removed, modified=modified,
        warnings=list(getattr(base_report, 'warnings', []) or []),
        failures=list(getattr(base_report, 'failures', []) or []),
    )


def _index_equipmod_family(tree_root: Optional[ET.Element],
                           family: str,
                           internal_label: str) -> dict[tuple, ET.Element]:
    """Index leaf mod entries under DIRECT `<family>` children of the root.

    A family element (`<weapon>`, `<shield>`, ...) has children whose tag
    names are stat labels (`<damage>`, `<cooling>`, ...) and whose `@ware`
    identifies the mod. Walk direct children; skip ones without `@ware`.
    Scans only direct children of `tree_root` so a bonus sub-element tag
    never gets confused for a family element.
    """
    out: dict[tuple, ET.Element] = {}
    if tree_root is None:
        return out
    for family_el in tree_root:
        if family_el.tag != family:
            continue
        for leaf in family_el:
            ware = leaf.get('ware')
            if ware is None:
                continue
            quality = leaf.get('quality')
            key = (internal_label, family, ware, quality)
            out[key] = leaf
    return out


def _equipmod_key_sort(k: tuple) -> tuple:
    """Stable sort key — coerce None elements to '' so sorting doesn't
    crash on mixed None/str tuples."""
    return tuple('' if x is None else str(x) for x in k)


def _emit_equipmod(report, family: str, internal_label: str) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_equipmod_row(rec, 'added', family, internal_label))
    for rec in report.removed:
        outputs.append(_equipmod_row(rec, 'removed', family, internal_label))
    for rec in report.modified:
        row = _equipmod_row(rec, 'modified', family, internal_label)
        if row is not None:
            outputs.append(row)
    return outputs


def _equipmod_row(rec, kind: str, family: str,
                  internal_label: str) -> Optional[RuleOutput]:
    classifications = ['equipmod', family]
    _, _, ware, _ = rec.key
    if kind == 'added':
        fields = _collect_attrs(rec.element, EQUIPMOD_LEAF_ATTRS)
        bonus_fields = _equipmod_bonus_fields(rec.element)
        parts = ['NEW']
        parts.extend(f'{k}={v}' for k, v in sorted(fields.items()))
        for bkey in sorted(bonus_fields.keys()):
            parts.append(f'{bkey}={bonus_fields[bkey]}')
        srcs = render_sources(None, rec.sources)
        old_srcs, new_srcs = None, rec.sources
    elif kind == 'removed':
        parts = ['REMOVED']
        srcs = render_sources(rec.sources, None)
        old_srcs, new_srcs = rec.sources, None
    else:  # modified
        old_fields = _collect_attrs(rec.old, EQUIPMOD_LEAF_ATTRS)
        new_fields = _collect_attrs(rec.new, EQUIPMOD_LEAF_ATTRS)
        old_bonus = _equipmod_bonus_fields(rec.old)
        new_bonus = _equipmod_bonus_fields(rec.new)
        changes = _diff_attr_map(old_fields, new_fields)
        changes.extend(_diff_attr_map(old_bonus, new_bonus))
        if not changes:
            return None
        parts = changes
        srcs = render_sources(rec.old_sources, rec.new_sources)
        old_srcs, new_srcs = rec.old_sources, rec.new_sources

    text = _format(ware, classifications, srcs, parts)
    extras = {
        'entity_key': rec.key,
        'kind': kind,
        'subsource': internal_label,
        'classifications': classifications,
        'family': family,
        'ware': ware,
    }
    if old_srcs is not None:
        extras['old_sources'] = list(old_srcs)
    if new_srcs is not None:
        extras['new_sources'] = list(new_srcs)
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _equipmod_bonus_fields(leaf: ET.Element) -> dict[str, str]:
    """Flatten a leaf mod's `<bonus>` children to dotted keys.

    Real XML shape:
      <damage ware="X" quality="1" min="1.35" max="1.45">
        <bonus chance="1.0" max="1">
          <cooling min="0.684" max="0.736"/>
        </bonus>
      </damage>

    We key each bonus sub-child by its tag name ("bonus type" per plan).
    Bonus-level attrs (`@chance`, `@max`) attach to each sub-child under
    that bonus. Bonus sub-tags are unique per leaf mod in real 9.00B6
    data (verified during rule design) so one flat dict suffices.

    Output keys (for `<cooling min="X" max="Y"/>` under
    `<bonus chance="1.0" max="1">`):
      bonus[type=cooling].chance       → 1.0
      bonus[type=cooling].max_enclosing → 1   (enclosing <bonus @max>)
      bonus[type=cooling].min          → 0.684
      bonus[type=cooling].max          → 0.736
      bonus[type=cooling].weight       → ...  (when present)
    """
    out: dict[str, str] = {}
    for bonus in leaf.findall('bonus'):
        enc_attrs: dict[str, str] = {}
        for a in BONUS_ATTRS:
            v = bonus.get(a)
            if v is not None:
                # Rename enclosing-bonus `@max` to avoid colliding with
                # the inner typed-child `@max`.
                label = 'max_enclosing' if a == 'max' else a
                enc_attrs[label] = v
        for inner in bonus:
            type_tag = inner.tag
            for a, v in enc_attrs.items():
                out[f'bonus[type={type_tag}].{a}'] = v
            for a in BONUS_INNER_ATTRS:
                v = inner.get(a)
                if v is not None:
                    out[f'bonus[type={type_tag}].{a}'] = v
    return out


# ---------- shared helpers ----------


def _collect_attrs(elem: ET.Element, attrs: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in attrs:
        v = elem.get(a)
        if v is not None:
            out[a] = v
    return out


def _diff_attr_map(old_map: dict[str, str],
                   new_map: dict[str, str]) -> list[str]:
    """Compare two flat attr maps; emit `key ov→nv` labels for differences."""
    out: list[str] = []
    for k in sorted(set(old_map) | set(new_map)):
        ov = old_map.get(k)
        nv = new_map.get(k)
        if ov == nv:
            continue
        out.append(f'{k} {ov}→{nv}')
    return out


def _diagnostic_key(text: str) -> tuple:
    short = sha256(text.encode('utf-8')).hexdigest()[:12]
    return ('diagnostic', TAG, short)


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'
