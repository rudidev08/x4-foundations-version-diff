"""Wares (non-equipment) rule: emit outputs for economy/trade ware changes.

Ware-driven — iterates every ware the `_wave1_common.owns` predicate routes to
`'wares'` (the residual bucket after ships, shields/missiles, spacesuit /
personalupgrade / satellites, and the other group-named rules have claimed
their wares). For each changed ware:

- Display name via `resolve_attr_ref(ware, locale, attribute='name')` (ware `@name`
  carries the `{page,id}` ref; usually page `20201`).
- Classifications: `[@group, ...tags_tokens]` (generic `ware` filtered).
- Ware stat diff: `price` (min/average/max), `volume`, `@transport`.
- Production diff keyed by `@method` (via shared `diff_productions`).
- Owner-faction set diff: `<owner @faction>` — adds/removes reported as one
  `owner_factions added={...} removed={...}` label.
- Deprecation toggle on ware `@tags`.

Pure ware-level — NO macro resolution. The wares rule doesn't own any on-disk
component macro; `<container ref>` values here point at generic pickup macros
that the other rules don't diff either.
"""
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.macro_diff import diff_labels
from src.lib.rule_output import RuleOutput, format_row, render_sources
from src.rules._wave1_common import owns, diff_productions


TAG = 'wares'
LOCALE_PAGE = 20201

# (xpath_or_dot, attribute, label). '.' means attribute lives on the ware root.
WARE_STATS = [
    ('price', 'min', 'price_min'),
    ('price', 'average', 'price_avg'),
    ('price', 'max', 'price_max'),
    ('.', 'volume', 'volume'),
    ('.', 'transport', 'transport'),
]

# `ware` is on every ware's tags attribute (from the selection); strip it as
# a generic token so classifications stay meaningful.
_GENERIC_FILTER = frozenset({'ware'})


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit wares rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused (ware-driven).
    """
    outputs: list[RuleOutput] = []
    locale_old, locale_new = Locale.build_pair(old_root, new_root, outputs, tag=TAG)

    def _key_fn(e: ElementTree.Element):
        return e.get('id') if owns(e, TAG) else None

    report = diff_library(
        old_root, new_root, 'libraries/wares.xml', './/ware',
        key_fn=_key_fn, key_fn_identity=f'{TAG}_ware_id',
    )
    for record in report.added:
        outputs.extend(_emit_added(record, locale_new))
    for record in report.removed:
        outputs.extend(_emit_removed(record, locale_old))
    for record in report.modified:
        outputs.extend(_emit_modified(record, locale_old, locale_new))

    forward_incomplete(report, outputs, tag=TAG)
    forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


def _classify(ware: ElementTree.Element) -> list[str]:
    """Return `[@group, ...tag_tokens]` for a ware. Missing group omitted.

    Example: `<ware group="food" tags="economy container">` →
    `['food', 'container', 'economy']`. Generic filter drops `ware` if present;
    `deprecated` drops (lifecycle, not a descriptor); group is not duplicated
    if the same token also appears in tags.
    """
    out: list[str] = []
    group = ware.get('group')
    if group:
        out.append(group)
    tags = (ware.get('tags') or '').split()
    for t in sorted(set(tags)):
        if t in _GENERIC_FILTER or t == 'deprecated':
            continue
        if t == group:
            continue
        out.append(t)
    return out


def _owner_factions(ware: ElementTree.Element) -> set[str]:
    return {o.get('faction') for o in ware.findall('owner') if o.get('faction')}


def _owner_diff(old_ware: ElementTree.Element, new_ware: ElementTree.Element) -> Optional[str]:
    old_set = _owner_factions(old_ware)
    new_set = _owner_factions(new_ware)
    added = new_set - old_set
    removed = old_set - new_set
    if not added and not removed:
        return None
    parts: list[str] = []
    if added:
        parts.append('added={' + ','.join(sorted(added)) + '}')
    if removed:
        parts.append('removed={' + ','.join(sorted(removed)) + '}')
    return 'owner_factions ' + ' '.join(parts)


def _emit_added(record, locale_new: Locale) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    name = resolve_attr_ref(ware, locale_new, attribute='name', fallback=ware_id)
    classifications = _classify(ware)
    sources_label = render_sources(None, record.sources)
    deprecated = 'deprecated' in (ware.get('tags') or '').split()
    parts = ['NEW']
    if deprecated:
        parts.append('already deprecated on release')
    text = format_row(TAG, name, classifications, sources_label, parts)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'kind': 'added',
        'classifications': classifications,
        'new_sources': list(record.sources),
        'source': record.sources,
        'sources': record.sources,
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_removed(record, locale_old: Locale) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    name = resolve_attr_ref(ware, locale_old, attribute='name', fallback=ware_id)
    classifications = _classify(ware)
    sources_label = render_sources(record.sources, None)
    text = format_row(TAG, name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'kind': 'removed',
        'classifications': classifications,
        'old_sources': list(record.sources),
        'source': record.sources,
        'sources': record.sources,
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_modified(record, locale_old: Locale, locale_new: Locale) -> list[RuleOutput]:
    ware_id = record.key
    name = resolve_attr_ref(record.new, locale_new, attribute='name', fallback=ware_id)
    if name == ware_id:
        name = resolve_attr_ref(record.old, locale_old, attribute='name', fallback=ware_id)
    # Classifications reflect the NEW side; if tags cleared but the ware still
    # exists, _classify on new gets the post-change shape.
    classifications = _classify(record.new)

    changes: list[str] = []
    changes.extend(diff_labels(record.old, record.new, WARE_STATS))
    changes.extend(diff_productions(record.old, record.new))
    owner_label = _owner_diff(record.old, record.new)
    if owner_label:
        changes.append(owner_label)

    # Tag-set changes (excluding lifecycle `deprecated`, handled below).
    old_tags = set((record.old.get('tags') or '').split())
    new_tags = set((record.new.get('tags') or '').split())
    added_tags = (new_tags - old_tags) - {'deprecated'}
    removed_tags = (old_tags - new_tags) - {'deprecated'}
    if added_tags or removed_tags:
        parts = []
        if added_tags:
            parts.append('added={' + ','.join(sorted(added_tags)) + '}')
        if removed_tags:
            parts.append('removed={' + ','.join(sorted(removed_tags)) + '}')
        changes.append('tags ' + ' '.join(parts))

    # Lifecycle (deprecation toggle) — prepended so it reads first.
    if 'deprecated' in new_tags and 'deprecated' not in old_tags:
        changes.insert(0, 'DEPRECATED')
    elif 'deprecated' in old_tags and 'deprecated' not in new_tags:
        changes.insert(0, 'un-deprecated')

    if not changes:
        return []

    sources_label = render_sources(record.old_sources, record.new_sources)
    text = format_row(TAG, name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'kind': 'modified',
        'classifications': classifications,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'source': record.new_sources,
        'ref_sources': dict(record.new_ref_sources),
    })]
