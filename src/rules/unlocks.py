"""Unlocks rule: emit outputs for discount, chapter, info-unlock changes.

Three sub-sources share the `unlocks` tag, distinguished by
`extras.subsource`:

- `discount` — `diff_library` on `libraries/unlocks.xml`. xpath `.//discount`,
  key `(subsource, @id)`. Display via locale page 20210 (`resolve_attr_ref`
  on `@name`). Diffs `<conditions>` and `<actions>` blocks, each indexed by
  child `@type` (tag-name of the child element). Parse-time assertions:
  duplicate child tags within a single `<conditions>` or `<actions>` block
  emit incomplete with reasons `'condition_type_not_unique'` and
  `'action_type_not_unique'` respectively.
- `chapter` — `diff_library` on `libraries/chapters.xml`. xpath `.//category`,
  key `(subsource, @id)`. Display via locale page 55101. Diffs `@group`,
  `@highlight`, `@teamware`.
- `info` — `diff_library` on `libraries/infounlocklist.xml`. xpath `.//info`,
  key `(subsource, @type)`. Display: `@type` verbatim (enum key, no locale).
  Diffs `@percent`.

All three sub-sources route failures through `forward_incomplete_many` with
per-subsource scoping; a patch error in one sub-source cannot contaminate
entities in another.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'unlocks'

# Classifications generic filter: `[<subsource>]` — all subsource tokens
# are information-bearing, so the filter is empty.
_GENERIC_FILTER: frozenset[str] = frozenset()

# Chapter attrs diffed directly.
_CHAPTER_ATTRS = ('group', 'highlight', 'teamware')


# ---------- synthetic sub-reports for rule-level assertions ----------


@dataclass
class _RuleReport:
    """Synthetic DiffReport-shaped wrapper for rule-level diagnostics.

    `diff_library` only emits failures for DLC patch / parse errors; the
    discount sub-source's uniqueness assertions live in a parallel bag that
    rides the same `forward_incomplete_many` pipeline so subsource scope
    stays intact.
    """
    failures: list[tuple[str, dict]] = field(default_factory=list)
    warnings: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


@dataclass
class _MergedReport:
    """Merge a DiffReport with a _RuleReport for a single
    `forward_incomplete` scope. Prefixes `affected_keys` from the underlying
    DiffReport's failures with the subsource tag so
    `entity_key=(subsource, id)` matches.
    """
    report: object
    subsource: str
    rule_report: _RuleReport = field(default_factory=_RuleReport)

    @property
    def incomplete(self) -> bool:
        return bool(getattr(self.report, 'incomplete', False)) or \
               bool(self.rule_report.failures)

    @property
    def failures(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for text, extras in list(getattr(self.report, 'failures', []) or []):
            new_extras = dict(extras)
            ak = new_extras.get('affected_keys') or []
            new_extras['affected_keys'] = [(self.subsource, k) for k in ak]
            out.append((text, new_extras))
        # Rule-level failures already carry the (subsource, id) tuple.
        out.extend(self.rule_report.failures)
        return out

    @property
    def warnings(self) -> list[tuple[str, dict]]:
        return list(getattr(self.report, 'warnings', []) or [])


# ---------- top-level entry ----------


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit unlocks rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; the rule is library-driven.
    """
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    discount_report = diff_library(
        old_root, new_root, 'libraries/unlocks.xml', './/discount',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='unlocks_discount',
    )
    discount_rule_report = _RuleReport()
    outputs.extend(_emit_discount(discount_report, loc_old, loc_new,
                                  discount_rule_report))
    _scan_discount_uniqueness(discount_report, discount_rule_report)

    chapter_report = diff_library(
        old_root, new_root, 'libraries/chapters.xml', './/category',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='unlocks_chapter',
    )
    outputs.extend(_emit_chapter(chapter_report, loc_old, loc_new))

    info_report = diff_library(
        old_root, new_root, 'libraries/infounlocklist.xml', './/info',
        key_fn=lambda e: e.get('type'),
        key_fn_identity='unlocks_info',
    )
    outputs.extend(_emit_info(info_report))

    forward_incomplete_many(
        [
            (_MergedReport(discount_report, 'discount',
                           discount_rule_report), 'discount'),
            (_MergedReport(chapter_report, 'chapter'), 'chapter'),
            (_MergedReport(info_report, 'info'), 'info'),
        ],
        outputs, tag=TAG,
    )
    for r in (discount_report, chapter_report, info_report):
        forward_warnings(r.warnings, outputs, tag=TAG)
    return outputs


# ---------- discount sub-source ----------


def _emit_discount(report, loc_old: Locale, loc_new: Locale,
                   rule_report: _RuleReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.extend(_emit_discount_added(rec, loc_new))
    for rec in report.removed:
        outputs.extend(_emit_discount_removed(rec, loc_old))
    for rec in report.modified:
        outputs.extend(_emit_discount_modified(rec, loc_old, loc_new))
    return outputs


def _emit_discount_added(rec, loc_new: Locale) -> list[RuleOutput]:
    discount = rec.element
    did = rec.key
    name = resolve_attr_ref(discount, loc_new, attr='name', fallback=did)
    classifications = ['discount']
    srcs = render_sources(None, rec.sources)
    text = _format(name, classifications, srcs, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('discount', did),
        'kind': 'added',
        'subsource': 'discount',
        'classifications': classifications,
        'discount_id': did,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_discount_removed(rec, loc_old: Locale) -> list[RuleOutput]:
    discount = rec.element
    did = rec.key
    name = resolve_attr_ref(discount, loc_old, attr='name', fallback=did)
    classifications = ['discount']
    srcs = render_sources(rec.sources, None)
    text = _format(name, classifications, srcs, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('discount', did),
        'kind': 'removed',
        'subsource': 'discount',
        'classifications': classifications,
        'discount_id': did,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_discount_modified(rec, loc_old: Locale,
                            loc_new: Locale) -> list[RuleOutput]:
    did = rec.key
    name = resolve_attr_ref(rec.new, loc_new, attr='name', fallback=did)
    if name == did:
        name = resolve_attr_ref(rec.old, loc_old, attr='name', fallback=did)
    classifications = ['discount']

    changes: list[str] = []
    # Top-level attrs on <discount>. @name is display (tracked via locale
    # resolution), @description is unused by UI; we still diff attrs just in
    # case the non-locale attrs (e.g., new experimental flags) appear.
    for a in sorted(set(rec.old.attrib) | set(rec.new.attrib)):
        if a in ('id', 'name', 'description'):
            continue
        ov = rec.old.get(a)
        nv = rec.new.get(a)
        if ov != nv:
            changes.append(f'{a} {ov}→{nv}')
    # <conditions> and <actions>: each is a singleton child; its own attrs
    # diff directly, and its child elements diff keyed by tag name.
    changes.extend(_diff_typed_block(rec.old, rec.new, 'conditions'))
    changes.extend(_diff_typed_block(rec.old, rec.new, 'actions'))

    if not changes:
        return []

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, srcs, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('discount', did),
        'kind': 'modified',
        'subsource': 'discount',
        'classifications': classifications,
        'discount_id': did,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]


def _diff_typed_block(old_parent: ET.Element, new_parent: ET.Element,
                      block_tag: str) -> list[str]:
    """Diff a singleton `<conditions>`/`<actions>` child:
    - Attrs on the block element itself (e.g., `<conditions weight="20">`).
    - Child entries keyed by their own tag name (the "type" per spec).
    """
    old_el = old_parent.find(block_tag)
    new_el = new_parent.find(block_tag)
    if old_el is None and new_el is None:
        return []
    out: list[str] = []
    if old_el is None:
        out.append(f'{block_tag} added')
        return out
    if new_el is None:
        out.append(f'{block_tag} removed')
        return out
    # Attrs on the block element itself.
    for a in sorted(set(old_el.attrib) | set(new_el.attrib)):
        ov = old_el.get(a)
        nv = new_el.get(a)
        if ov != nv:
            out.append(f'{block_tag} {a} {ov}→{nv}')
    # Child entries keyed by tag name. Uniqueness is validated by the
    # per-discount scan (see `_scan_discount_uniqueness`); this helper assumes
    # last-wins on collisions and leaves the diagnostic to the scanner.
    old_children = {c.tag: c for c in old_el}
    new_children = {c.tag: c for c in new_el}
    for tag in sorted(new_children.keys() - old_children.keys()):
        attrs = _fmt_attrs(new_children[tag].attrib)
        out.append(f'{block_tag}.{tag} added'
                   + (f' ({attrs})' if attrs else ''))
    for tag in sorted(old_children.keys() - new_children.keys()):
        attrs = _fmt_attrs(old_children[tag].attrib)
        out.append(f'{block_tag}.{tag} removed'
                   + (f' (was {attrs})' if attrs else ''))
    for tag in sorted(old_children.keys() & new_children.keys()):
        o_el = old_children[tag]
        n_el = new_children[tag]
        for a in sorted(set(o_el.attrib) | set(n_el.attrib)):
            ov = o_el.get(a)
            nv = n_el.get(a)
            if ov != nv:
                out.append(f'{block_tag}.{tag} {a} {ov}→{nv}')
    return out


def _fmt_attrs(attrib: dict) -> str:
    return ', '.join(f'{k}={v}' for k, v in sorted(attrib.items()))


def _scan_discount_uniqueness(report, rule_report: _RuleReport) -> None:
    """Walk both effective trees, checking child-tag uniqueness within each
    discount's `<conditions>` and `<actions>` blocks. Duplicate tags violate
    the keyed-by-tag contract and emit an incomplete with reason
    `condition_type_not_unique` or `action_type_not_unique`. De-dup across
    both sides so unchanged malformed discounts don't double-report.
    """
    seen_cond: set[tuple[str, str]] = set()
    seen_act: set[tuple[str, str]] = set()
    for root in (report.effective_old_root, report.effective_new_root):
        if root is None:
            continue
        for discount in root.iter('discount'):
            did = discount.get('id')
            if did is None:
                continue
            _check_block_uniqueness(discount, did, 'conditions',
                                    'condition_type_not_unique',
                                    rule_report, seen_cond)
            _check_block_uniqueness(discount, did, 'actions',
                                    'action_type_not_unique',
                                    rule_report, seen_act)


def _check_block_uniqueness(discount: Optional[ET.Element], did: str,
                            block_tag: str, reason: str,
                            rule_report: _RuleReport,
                            dedup: Optional[set] = None) -> None:
    if discount is None:
        return
    block = discount.find(block_tag)
    if block is None:
        return
    counts: dict[str, int] = {}
    for child in block:
        counts[child.tag] = counts.get(child.tag, 0) + 1
    dupes = sorted([tag for tag, c in counts.items() if c > 1])
    for tag in dupes:
        key = (did, tag)
        if dedup is not None:
            if key in dedup:
                continue
            dedup.add(key)
        rule_report.failures.append((
            f'discount {did} <{block_tag}> has duplicate <{tag}>',
            {
                'reason': reason,
                'discount_id': did,
                'block': block_tag,
                'duplicate_type': tag,
                'affected_keys': [('discount', did)],
            },
        ))


# ---------- chapter sub-source ----------


def _emit_chapter(report, loc_old: Locale,
                  loc_new: Locale) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_emit_chapter_added(rec, loc_new))
    for rec in report.removed:
        outputs.append(_emit_chapter_removed(rec, loc_old))
    for rec in report.modified:
        row = _emit_chapter_modified(rec, loc_old, loc_new)
        if row is not None:
            outputs.append(row)
    return outputs


def _emit_chapter_added(rec, loc_new: Locale) -> RuleOutput:
    cat = rec.element
    cid = rec.key
    name = resolve_attr_ref(cat, loc_new, attr='name', fallback=cid)
    classifications = ['chapter']
    srcs = render_sources(None, rec.sources)
    text = _format(name, classifications, srcs, ['NEW'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('chapter', cid),
        'kind': 'added',
        'subsource': 'chapter',
        'classifications': classifications,
        'chapter_id': cid,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })


def _emit_chapter_removed(rec, loc_old: Locale) -> RuleOutput:
    cat = rec.element
    cid = rec.key
    name = resolve_attr_ref(cat, loc_old, attr='name', fallback=cid)
    classifications = ['chapter']
    srcs = render_sources(rec.sources, None)
    text = _format(name, classifications, srcs, ['REMOVED'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('chapter', cid),
        'kind': 'removed',
        'subsource': 'chapter',
        'classifications': classifications,
        'chapter_id': cid,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })


def _emit_chapter_modified(rec, loc_old: Locale,
                           loc_new: Locale) -> Optional[RuleOutput]:
    cid = rec.key
    name = resolve_attr_ref(rec.new, loc_new, attr='name', fallback=cid)
    if name == cid:
        name = resolve_attr_ref(rec.old, loc_old, attr='name', fallback=cid)
    classifications = ['chapter']

    changes: list[str] = []
    for a in _CHAPTER_ATTRS:
        ov = rec.old.get(a)
        nv = rec.new.get(a)
        if ov != nv:
            changes.append(f'{a} {ov}→{nv}')

    if not changes:
        return None

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, srcs, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('chapter', cid),
        'kind': 'modified',
        'subsource': 'chapter',
        'classifications': classifications,
        'chapter_id': cid,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })


# ---------- info sub-source ----------


def _emit_info(report) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.append(_emit_info_added(rec))
    for rec in report.removed:
        outputs.append(_emit_info_removed(rec))
    for rec in report.modified:
        row = _emit_info_modified(rec)
        if row is not None:
            outputs.append(row)
    return outputs


def _emit_info_added(rec) -> RuleOutput:
    info = rec.element
    itype = rec.key
    classifications = ['info']
    srcs = render_sources(None, rec.sources)
    percent = info.get('percent')
    parts = ['NEW']
    if percent is not None:
        parts.append(f'percent={percent}')
    text = _format(itype, classifications, srcs, parts)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('info', itype),
        'kind': 'added',
        'subsource': 'info',
        'classifications': classifications,
        'info_type': itype,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })


def _emit_info_removed(rec) -> RuleOutput:
    itype = rec.key
    classifications = ['info']
    srcs = render_sources(rec.sources, None)
    text = _format(itype, classifications, srcs, ['REMOVED'])
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('info', itype),
        'kind': 'removed',
        'subsource': 'info',
        'classifications': classifications,
        'info_type': itype,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })


def _emit_info_modified(rec) -> Optional[RuleOutput]:
    itype = rec.key
    classifications = ['info']

    changes: list[str] = []
    ov = rec.old.get('percent')
    nv = rec.new.get('percent')
    if ov != nv:
        changes.append(f'percent {ov}→{nv}')

    if not changes:
        return None

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(itype, classifications, srcs, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('info', itype),
        'kind': 'modified',
        'subsource': 'info',
        'classifications': classifications,
        'info_type': itype,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })


# ---------- formatting ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'
