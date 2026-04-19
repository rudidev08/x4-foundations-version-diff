"""Equipment rule: emit outputs for equipment ware changes.

Ware-driven (ware-only) — iterates every ware the shared `_wave1_common.owns`
predicate routes to `'equipment'` (software, hardware, countermeasures,
satellite_*, spacesuit gear, personalupgrade tag) via `diff_library`.

Equipment is scope-limited to WARE-LEVEL diffs only. Macros are NOT diffed —
equipment wares reference a heterogeneous soup of macros (software bundles,
scanner arrays, scanner object discs, satellites, spacesuit engines/weapons)
and there is no single stat tuple that applies across the set.

Instead, when a file under the equipment macro tree changes, the rule emits a
loud WARNING (one per impacted ware) so nothing falls through silently — the
LLM-summary stage sees the macro file changed and can reason about it.

Locale resolution sticks with `resolve_attr_ref` rather than a page-picking
heuristic; counterexamples like `bomb_player_limpet_emp_01_mk1` and
`software_scannerobjectmk3` document why heuristics break here.
"""
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.macro_diff import diff_labels
from src.lib.rule_output import RuleOutput, diagnostic_entity_key, format_row, render_sources
from src.rules._wave1_common import (
    diff_productions,
    equipment_macro_reverse_index,
    owns,
)


TAG = 'equipment'

# (xpath_or_dot, attribute, label). '.' = attribute on the ware root.
WARE_STATS = [
    ('price', 'min', 'price_min'),
    ('price', 'average', 'price_avg'),
    ('price', 'max', 'price_max'),
    ('.', 'volume', 'volume'),
    ('.', 'transport', 'transport'),
]


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit equipment rule outputs for old_root → new_root.

    `changes` is a `list[FileChange]` from `change_map.build`. Used ONLY to
    drive the macro-gap warnings — ware rows derive from `diff_library`.
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

    if changes:
        _emit_macro_gap_warnings(old_root, new_root, changes, outputs)

    forward_incomplete(report, outputs, tag=TAG)
    forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


def _effective_category(ware: ElementTree.Element) -> str:
    """Match `_wave1_common.ware_owner`'s equipment branch, in English.

    Ordering mirrors ware_owner: spacesuit/personalupgrade first, then
    satellite_, then @group dispatch. A ware can only route to equipment via
    one of these branches (ware_owner short-circuits), so the returned label
    is unambiguous.
    """
    tags = (ware.get('tags') or '').split()
    group = ware.get('group')
    ware_id = ware.get('id') or ''
    if 'personalupgrade' in tags or 'spacesuit' in ware_id.split('_'):
        return 'spacesuit'
    if ware_id.startswith('satellite_'):
        return 'satellite'
    if group == 'software':
        return 'software'
    if group == 'hardware':
        return 'hardware'
    if group == 'countermeasures':
        return 'countermeasures'
    # Ownership predicate shouldn't route here otherwise, but stay honest.
    return group or 'equipment'


def _classify(ware: ElementTree.Element) -> list[str]:
    """`[<effective_category>, ...markers]`. The `<group>_origin` marker is
    added when a spacesuit/personalupgrade ware kept its original @group
    (e.g. spacesuit engine) so the LLM can see both facets.
    """
    cat = _effective_category(ware)
    markers: list[str] = []
    if cat == 'spacesuit':
        group = ware.get('group')
        if group:
            markers.append(f'{group}_origin')
    return [cat, *markers]


def _owner_factions(ware: ElementTree.Element) -> set[str]:
    return {o.get('faction') for o in ware.findall('owner') if o.get('faction')}


def _diff_owners(old_ware: ElementTree.Element, new_ware: ElementTree.Element) -> list[str]:
    old, new = _owner_factions(old_ware), _owner_factions(new_ware)
    out: list[str] = []
    for f in sorted(new - old):
        out.append(f'owner.{f} added')
    for f in sorted(old - new):
        out.append(f'owner.{f} removed')
    return out


def _diff_tags(old_ware: ElementTree.Element, new_ware: ElementTree.Element) -> list[str]:
    old = set((old_ware.get('tags') or '').split())
    new = set((new_ware.get('tags') or '').split())
    out: list[str] = []
    for t in sorted(new - old):
        out.append(f'tag.{t} added')
    for t in sorted(old - new):
        out.append(f'tag.{t} removed')
    return out


def _emit_added(record, locale_new: Locale) -> list[RuleOutput]:
    ware = record.element
    ware_id = record.key
    name = resolve_attr_ref(ware, locale_new, attribute='name', fallback=ware_id)
    classifications = _classify(ware)
    sources_label = render_sources(None, record.sources)
    text = format_row(TAG, name, classifications, sources_label, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'ware_id': ware_id,
        'kind': 'added',
        'classifications': classifications,
        'sources': list(record.sources),
        'source_files': list(record.source_files),
        'new_sources': list(record.sources),
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
        'ware_id': ware_id,
        'kind': 'removed',
        'classifications': classifications,
        'sources': list(record.sources),
        'source_files': list(record.source_files),
        'old_sources': list(record.sources),
        'ref_sources': dict(record.ref_sources),
    })]


def _emit_modified(record, locale_old: Locale, locale_new: Locale) -> list[RuleOutput]:
    ware_id = record.key
    name = resolve_attr_ref(record.new, locale_new, attribute='name', fallback=ware_id)
    if name == ware_id:
        name = resolve_attr_ref(record.old, locale_old, attribute='name', fallback=ware_id)
    classifications = _classify(record.new)

    changes: list[str] = []
    changes.extend(diff_labels(record.old, record.new, WARE_STATS))
    changes.extend(diff_productions(record.old, record.new))
    changes.extend(_diff_owners(record.old, record.new))
    changes.extend(_diff_tags(record.old, record.new))

    old_tags = (record.old.get('tags') or '').split()
    new_tags = (record.new.get('tags') or '').split()
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
        'ware_id': ware_id,
        'kind': 'modified',
        'classifications': classifications,
        'old_source_files': list(record.old_source_files),
        'new_source_files': list(record.new_source_files),
        'old_sources': list(record.old_sources),
        'new_sources': list(record.new_sources),
        'ref_sources': dict(record.new_ref_sources),
    })]


def _is_candidate_macro_path(path: str) -> bool:
    """True when the changed file COULD name an equipment macro.

    Heuristic mirrors what the reverse index keys on (macro_ref = filename
    stem). Scope: `_macro.xml` files under any assets dir OR any file under
    `assets/props/` / `assets/fx/`. Rejects `libraries/`, locale files,
    `md/`, etc. — filenames that couldn't possibly be macro refs.
    """
    if path.endswith('_macro.xml'):
        return True
    if '/assets/props/' in path or '/assets/fx/' in path:
        return True
    if path.startswith('assets/props/') or path.startswith('assets/fx/'):
        return True
    return False


def _emit_macro_gap_warnings(old_root: Path, new_root: Path,
                             changes, outputs: list[RuleOutput]) -> None:
    """For every changed file whose stem is referenced by an equipment ware
    (on either side), emit one warning per impacted ware.

    Independent of ware rows — both can emit for the same ware.
    """
    old_idx = equipment_macro_reverse_index(old_root)
    new_idx = equipment_macro_reverse_index(new_root)
    seen: set[tuple[str, str]] = set()
    for fc in changes:
        p = fc.path
        if not _is_candidate_macro_path(p):
            continue
        stem = Path(p).stem
        impacted = set(old_idx.get(stem, [])) | set(new_idx.get(stem, []))
        for ware_id in sorted(impacted):
            if not ware_id:
                continue
            key = (p, ware_id)
            if key in seen:
                continue
            seen.add(key)
            text = (f'equipment macro {p} changed but equipment rule does '
                    f'not diff macros; ware={ware_id}')
            outputs.append(RuleOutput(tag=TAG, text=f'[{TAG}] WARNING: {text}',
                                      extras={
                'entity_key': diagnostic_entity_key(TAG, text),
                'macro_path': p,
                'ware_id': ware_id,
                'kind': 'warning',
                'subsource': 'diagnostic',
                'classifications': [],
                'warning': True,
            }))
