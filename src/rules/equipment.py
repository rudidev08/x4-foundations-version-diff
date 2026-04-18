"""Equipment rule: emit outputs for equipment ware changes.

Ware-driven (ware-only) — iterates every ware the Wave 1 ownership predicate
routes to `'equipment'` (software, hardware, countermeasures, satellite_*,
spacesuit gear, personalupgrade tag) via `diff_library`.

Equipment is scope-limited to WARE-LEVEL diffs only. Macros are NOT diffed —
equipment wares reference a heterogeneous soup of macros (software bundles,
scanner arrays, scanner object discs, satellites, spacesuit engines/weapons)
and there is no single stat tuple that applies across the set.

Instead, when a file under the equipment macro tree changes, the rule emits a
loud WARNING (one per impacted ware) so nothing falls through silently — the
LLM-summary stage sees the macro file changed and can reason about it.

Rationale for ware-only scope + warning: see plan Task 1.4 "Macro-gap warning
algorithm"; counterexamples for heuristic locale-page dispatch (bomb_player_limpet_emp_01_mk1,
software_scannerobjectmk3) document why we use `resolve_attr_ref` over any
page-picking heuristic.
"""
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.rule_output import RuleOutput, diagnostic_entity_key, render_sources
from src.rules._wave1_common import (
    diff_productions,
    equipment_macro_reverse_index,
    owns,
)


TAG = 'equipment'

# (xpath_or_dot, attr, label). '.' = attribute on the ware root.
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
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    def _key_fn(e: ET.Element):
        return e.get('id') if owns(e, TAG) else None

    report = diff_library(
        old_root, new_root, 'libraries/wares.xml', './/ware',
        key_fn=_key_fn, key_fn_identity=f'{TAG}_ware_id',
    )
    for rec in report.added:
        outputs.extend(_emit_added(rec, loc_new))
    for rec in report.removed:
        outputs.extend(_emit_removed(rec, loc_old))
    for rec in report.modified:
        outputs.extend(_emit_modified(rec, loc_old, loc_new))

    if changes:
        _emit_macro_gap_warnings(old_root, new_root, changes, outputs)

    forward_incomplete(report, outputs, tag=TAG)
    forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


# ---------- classification ----------


def _effective_category(ware: ET.Element) -> str:
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


def _classify(ware: ET.Element) -> list[str]:
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


# ---------- ware-level helpers ----------


def _elem_attr_root(ware: ET.Element, xpath: str, attr: str) -> Optional[str]:
    if xpath == '.':
        return ware.get(attr)
    el = ware.find(xpath)
    return None if el is None else el.get(attr)


def _ware_stat_diff(old_ware: ET.Element, new_ware: ET.Element) -> list[str]:
    out: list[str] = []
    for xpath, attr, label in WARE_STATS:
        ov = _elem_attr_root(old_ware, xpath, attr)
        nv = _elem_attr_root(new_ware, xpath, attr)
        if ov != nv:
            out.append(f'{label} {ov}→{nv}')
    return out


def _owner_factions(ware: ET.Element) -> set[str]:
    return {o.get('faction') for o in ware.findall('owner') if o.get('faction')}


def _diff_owners(old_ware: ET.Element, new_ware: ET.Element) -> list[str]:
    old, new = _owner_factions(old_ware), _owner_factions(new_ware)
    out: list[str] = []
    for f in sorted(new - old):
        out.append(f'owner.{f} added')
    for f in sorted(old - new):
        out.append(f'owner.{f} removed')
    return out


def _diff_tags(old_ware: ET.Element, new_ware: ET.Element) -> list[str]:
    old = set((old_ware.get('tags') or '').split())
    new = set((new_ware.get('tags') or '').split())
    out: list[str] = []
    for t in sorted(new - old):
        out.append(f'tag.{t} added')
    for t in sorted(old - new):
        out.append(f'tag.{t} removed')
    return out


# ---------- emitters ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    return f'[{TAG}] {name}{cls} {sources_label}: {", ".join(parts)}'


def _emit_added(rec, loc_new: Locale) -> list[RuleOutput]:
    ware = rec.element
    ware_id = rec.key
    name = resolve_attr_ref(ware, loc_new, attr='name', fallback=ware_id)
    classifications = _classify(ware)
    sources_label = render_sources(None, rec.sources)
    text = _format(name, classifications, sources_label, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'ware_id': ware_id,
        'kind': 'added',
        'classifications': classifications,
        'sources': list(rec.sources),
        'source_files': list(rec.source_files),
        'new_sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_removed(rec, loc_old: Locale) -> list[RuleOutput]:
    ware = rec.element
    ware_id = rec.key
    name = resolve_attr_ref(ware, loc_old, attr='name', fallback=ware_id)
    classifications = _classify(ware)
    sources_label = render_sources(rec.sources, None)
    text = _format(name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'ware_id': ware_id,
        'kind': 'removed',
        'classifications': classifications,
        'sources': list(rec.sources),
        'source_files': list(rec.source_files),
        'old_sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_modified(rec, loc_old: Locale, loc_new: Locale) -> list[RuleOutput]:
    ware_id = rec.key
    name = resolve_attr_ref(rec.new, loc_new, attr='name', fallback=ware_id)
    if name == ware_id:
        name = resolve_attr_ref(rec.old, loc_old, attr='name', fallback=ware_id)
    classifications = _classify(rec.new)

    changes: list[str] = []
    changes.extend(_ware_stat_diff(rec.old, rec.new))
    changes.extend(diff_productions(rec.old, rec.new))
    changes.extend(_diff_owners(rec.old, rec.new))
    changes.extend(_diff_tags(rec.old, rec.new))

    old_tags = (rec.old.get('tags') or '').split()
    new_tags = (rec.new.get('tags') or '').split()
    if 'deprecated' in new_tags and 'deprecated' not in old_tags:
        changes.insert(0, 'DEPRECATED')
    elif 'deprecated' in old_tags and 'deprecated' not in new_tags:
        changes.insert(0, 'un-deprecated')

    if not changes:
        return []

    sources_label = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ware_id,
        'ware_id': ware_id,
        'kind': 'modified',
        'classifications': classifications,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]


# ---------- macro-gap warnings ----------


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
