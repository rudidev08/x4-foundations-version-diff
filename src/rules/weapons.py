"""Weapons rule.

See src/rules/weapons.md for the data model and classification policy.
Follows the Canonical RuleOutput schema defined in the implementation plan.

Ware-driven: iterates `group="weapons"` wares (including mines) across old +
new `libraries/wares.xml` (core + DLC) via `diff_library`.

For each weapon:
- Ware-level diffs: price (min/average/max), volume, tags, keyed productions.
- Macro-level diffs: `<bullet @class>`, `<heat @overheat/@coolrate/@cooldelay>`,
  `<rotationspeed @max>`, `<hull @max>`.
- Bullet-macro diffs (subsource="bullet"): `<ammunition @value>` (damage) and
  `<bullet @speed/@lifetime/@amount/@barrelamount/@timediff/@reload/@heat>`.
  Bullets fan out 1:N — a single bullet macro can back multiple weapons.
  Impacted-ware set is the UNION of old + new reverse indices.
"""
from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.macro_diff import diff_attrs
from src.lib.paths import resolve_macro_path
from src.lib.rule_output import RuleOutput, render_sources
from src.rules._wave1_common import owns, diff_productions


TAG = 'weapons'
LOCALE_PAGE = 20105  # Weapons and Turrets page (display names).

WARE_STATS: list[tuple[str, str, str]] = [
    ('price', 'min',     'price_min'),
    ('price', 'average', 'price_avg'),
    ('price', 'max',     'price_max'),
    ('.',     'volume',  'volume'),
]

MACRO_STATS: list[tuple[str, str, str]] = [
    ('properties/bullet',         'class',       'bullet_class'),
    ('properties/heat',           'overheat',    'overheat'),
    ('properties/heat',           'coolrate',    'coolrate'),
    ('properties/heat',           'cooldelay',   'cooldelay'),
    ('properties/rotationspeed',  'max',         'rotation'),
    ('properties/hull',           'max',         'HP'),
]

BULLET_STATS: list[tuple[str, str, str]] = [
    ('properties/ammunition', 'value',        'damage'),
    ('properties/bullet',     'speed',        'speed'),
    ('properties/bullet',     'lifetime',     'lifetime'),
    ('properties/bullet',     'amount',       'amount'),
    ('properties/bullet',     'barrelamount', 'barrelamount'),
    ('properties/bullet',     'timediff',     'timediff'),
    ('properties/bullet',     'reload',       'reload'),
    ('properties/bullet',     'heat',         'heat'),
]

GENERIC_CLASSIFICATION_TOKENS = frozenset({'weapon', 'component'})

_MINE_ID_RE = re.compile(r'^weapon_.*_mine_')


@dataclass
class _MacroInfo:
    element: Optional[ET.Element]
    path: Optional[Path]
    subtype: Optional[str]


def run(old_root: Path, new_root: Path,
        changes: list | None = None) -> list[RuleOutput]:
    """Iterate weapon wares and emit outputs for adds/removes/modifications.

    `changes` kept for uniform rule interface; weapons is primarily ware-driven
    plus a bullet-macro fan-out augmentation (see `_emit_bullet_fanout`).
    """
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []

    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    def _key_fn(e):
        if not owns(e, TAG):
            return None
        return e.get('id')

    ware_report = diff_library(
        old_root, new_root, 'libraries/wares.xml', './/ware',
        key_fn=_key_fn,
        key_fn_identity=f'{TAG}_ware_id',
    )

    for rec in ware_report.added:
        outputs.extend(_emit_added(new_root, rec, loc_new))
    for rec in ware_report.removed:
        outputs.extend(_emit_removed(old_root, rec, loc_old))
    for rec in ware_report.modified:
        outputs.extend(_emit_modified(old_root, new_root, rec, loc_old, loc_new))

    # Bullet fan-out: emit a `subsource='bullet'` row per (impacted weapon,
    # bullet change). Uses union of old+new reverse indices so a weapon seen
    # on either side picks up the bullet change.
    outputs.extend(_emit_bullet_fanout(
        old_root, new_root, ware_report, loc_old, loc_new,
    ))

    forward_incomplete(ware_report, outputs, tag=TAG)
    forward_warnings(ware_report.warnings, outputs, tag=TAG)
    return outputs


# ---------- emit helpers ----------


def _emit_added(new_root: Path, rec, loc_new: Locale) -> list[RuleOutput]:
    info = _resolve_macro_info(new_root, rec, side='new')
    name = _display_name(rec, info, None, loc_new, loc_new)
    classifications = _classify(rec.key, rec.element, info)
    srcs = render_sources(None, rec.sources)
    text = _format(name, classifications, srcs, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': rec.key,
        'kind': 'added',
        'classifications': classifications,
        'new_source_files': rec.source_files,
        'new_sources': rec.sources,
        'sources': rec.sources,
        'ref_sources': rec.ref_sources,
    })]


def _emit_removed(old_root: Path, rec, loc_old: Locale) -> list[RuleOutput]:
    info = _resolve_macro_info(old_root, rec, side='old')
    name = _display_name(rec, info, None, loc_old, loc_old)
    classifications = _classify(rec.key, rec.element, info)
    srcs = render_sources(rec.sources, None)
    text = _format(name, classifications, srcs, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': rec.key,
        'kind': 'removed',
        'classifications': classifications,
        'old_source_files': rec.source_files,
        'old_sources': rec.sources,
        'sources': rec.sources,
        'ref_sources': rec.ref_sources,
    })]


def _emit_modified(old_root: Path, new_root: Path, rec,
                   loc_old: Locale, loc_new: Locale) -> list[RuleOutput]:
    old_info = _resolve_macro_info(old_root, rec, side='old')
    new_info = _resolve_macro_info(new_root, rec, side='new')
    name = _display_name(rec, new_info, old_info, loc_new, loc_old)
    classifications = _classify(
        rec.key, rec.new, new_info if new_info.element is not None else old_info)

    changes: list[str] = []
    for label, (ov, nv) in diff_attrs(rec.old, rec.new, WARE_STATS).items():
        changes.append(f'{label} {ov}→{nv}')
    changes.extend(diff_productions(rec.old, rec.new))

    old_tags = rec.old.get('tags') or ''
    new_tags = rec.new.get('tags') or ''
    if 'deprecated' in new_tags and 'deprecated' not in old_tags:
        changes.insert(0, 'DEPRECATED')
    elif 'deprecated' in old_tags and 'deprecated' not in new_tags:
        changes.insert(0, 'un-deprecated')

    if old_info.element is not None and new_info.element is not None:
        for label, (ov, nv) in diff_attrs(old_info.element,
                                           new_info.element, MACRO_STATS).items():
            changes.append(f'{label} {ov}→{nv}')

    if not changes:
        return []

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, srcs, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': rec.key,
        'kind': 'modified',
        'classifications': classifications,
        'old_source_files': rec.old_source_files,
        'new_source_files': rec.new_source_files,
        'old_sources': rec.old_sources,
        'new_sources': rec.new_sources,
        'sources': rec.new_sources,
        'ref_sources': rec.new_ref_sources,
    })]


# ---------- bullet fan-out ----------


def _build_bullet_reverse_indices(
        effective_root: Optional[ET.Element]) -> dict[str, list[str]]:
    """Build `{weapon_macro_ref: [weapon_ware_ids]}` from an effective wares tree.

    Walks every `ware[group="weapons"]` on the tree and maps its component
    ref (the weapon macro file stem) to the list of ware ids that point at
    it. One macro can back multiple wares so the value is a list.

    Bullet-level fan-out is a two-step join: weapon_macro_ref → weapon_ware_ids
    (this function), then bullet_ref → weapon_macro_refs (via `<bullet @class>`
    on each weapon macro). The caller composes them.
    """
    out: dict[str, list[str]] = {}
    if effective_root is None:
        return out
    for ware in effective_root.iter('ware'):
        if not owns(ware, TAG):
            continue
        comp = ware.find('component')
        if comp is None:
            continue
        ref = comp.get('ref')
        if not ref:
            continue
        wid = ware.get('id')
        if not wid:
            continue
        out.setdefault(ref, []).append(wid)
    return out


def _emit_bullet_fanout(old_root: Path, new_root: Path, ware_report,
                         loc_old: Locale, loc_new: Locale) -> list[RuleOutput]:
    """Emit per-weapon rows for bullet-macro stat changes.

    For bullet refs that are present on BOTH sides and whose referenced bullet
    macro has changed stats, emit one `subsource='bullet'` row per impacted
    weapon ware (UNION of old+new reverse indices).
    """
    old_macroref_to_wares = _build_bullet_reverse_indices(
        getattr(ware_report, 'effective_old_root', None))
    new_macroref_to_wares = _build_bullet_reverse_indices(
        getattr(ware_report, 'effective_new_root', None))

    old_bullet_refs: dict[str, list[str]] = {}
    new_bullet_refs: dict[str, list[str]] = {}

    def _populate(ware_root: Path, macroref_to_wares: dict[str, list[str]],
                  out: dict[str, list[str]]) -> None:
        for macro_ref, wids in macroref_to_wares.items():
            macro_path = resolve_macro_path(ware_root, ware_root, macro_ref,
                                             kind='weapons')
            if macro_path is None:
                continue
            macro_elem = _load_macro(macro_path)
            if macro_elem is None:
                continue
            bullet_el = macro_elem.find('properties/bullet')
            if bullet_el is None:
                continue
            bullet_ref = bullet_el.get('class')
            if not bullet_ref:
                continue
            out.setdefault(bullet_ref, []).extend(wids)

    _populate(old_root, old_macroref_to_wares, old_bullet_refs)
    _populate(new_root, new_macroref_to_wares, new_bullet_refs)

    # Ware-id → (kind, record) lookup.
    ware_records_by_key: dict[str, tuple[str, object]] = {}
    for rec in ware_report.modified:
        ware_records_by_key[rec.key] = ('modified', rec)
    for rec in ware_report.added:
        ware_records_by_key.setdefault(rec.key, ('added', rec))
    for rec in ware_report.removed:
        ware_records_by_key.setdefault(rec.key, ('removed', rec))

    outputs: list[RuleOutput] = []

    shared_bullet_refs = sorted(set(old_bullet_refs) & set(new_bullet_refs))
    for bullet_ref in shared_bullet_refs:
        old_bullet_path = resolve_macro_path(old_root, old_root, bullet_ref,
                                              kind='bullet')
        new_bullet_path = resolve_macro_path(new_root, new_root, bullet_ref,
                                              kind='bullet')
        if old_bullet_path is None or new_bullet_path is None:
            continue
        old_bullet = _load_macro(old_bullet_path)
        new_bullet = _load_macro(new_bullet_path)
        if old_bullet is None or new_bullet is None:
            continue
        deltas = diff_attrs(old_bullet, new_bullet, BULLET_STATS)
        if not deltas:
            continue

        impacted_wares = sorted(set(old_bullet_refs.get(bullet_ref, [])) |
                                set(new_bullet_refs.get(bullet_ref, [])))
        for wid in impacted_wares:
            row = _emit_bullet_row(
                wid, bullet_ref, deltas, ware_records_by_key,
                old_root, new_root, loc_old, loc_new,
            )
            if row is not None:
                outputs.append(row)
    return outputs


def _emit_bullet_row(wid: str, bullet_ref: str, deltas: dict,
                     ware_records_by_key: dict,
                     old_root: Path, new_root: Path,
                     loc_old: Locale, loc_new: Locale) -> Optional[RuleOutput]:
    kind_rec = ware_records_by_key.get(wid)
    if kind_rec is None:
        return None
    kind, rec = kind_rec
    if kind == 'modified':
        old_sources = rec.old_sources
        new_sources = rec.new_sources
        new_info = _resolve_macro_info(new_root, rec, side='new')
        old_info = _resolve_macro_info(old_root, rec, side='old')
        name = _display_name(rec, new_info, old_info, loc_new, loc_old)
        classifications = _classify(
            rec.key, rec.new, new_info if new_info.element is not None else old_info)
    elif kind == 'added':
        old_sources = None
        new_sources = rec.sources
        new_info = _resolve_macro_info(new_root, rec, side='new')
        name = _display_name(rec, new_info, None, loc_new, loc_new)
        classifications = _classify(rec.key, rec.element, new_info)
    else:  # removed
        old_sources = rec.sources
        new_sources = None
        old_info = _resolve_macro_info(old_root, rec, side='old')
        name = _display_name(rec, old_info, None, loc_old, loc_old)
        classifications = _classify(rec.key, rec.element, old_info)

    parts = [f'{label} {ov}→{nv}' for label, (ov, nv) in deltas.items()]
    srcs = render_sources(old_sources, new_sources)
    src_label = f' {srcs}' if srcs else ''
    text = f'[{TAG}] {name} (bullet){src_label}: {", ".join(parts)}'
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': rec.key,
        'kind': 'modified',
        'subsource': 'bullet',
        'classifications': classifications,
        'bullet_ref': bullet_ref,
        'old_sources': old_sources,
        'new_sources': new_sources,
        'sources': new_sources if new_sources is not None else old_sources,
    })


# ---------- resolution helpers ----------


def _resolve_macro_info(root: Path, rec, *, side: str) -> _MacroInfo:
    """Resolve (macro_path, macro_element, subtype) for this ware/side.

    `side` is 'old' or 'new' for ModifiedRecord; EntityRecord uses whichever
    is passed (both branches return the same rec.element).
    """
    if side == 'old':
        ref_sources = getattr(rec, 'old_ref_sources', None) or \
                      getattr(rec, 'ref_sources', None) or {}
        element = rec.old if hasattr(rec, 'old') else rec.element
    else:
        ref_sources = getattr(rec, 'new_ref_sources', None) or \
                      getattr(rec, 'ref_sources', None) or {}
        element = rec.new if hasattr(rec, 'new') else rec.element
    component = element.find('component')
    if component is None:
        return _MacroInfo(None, None, None)
    ref = component.get('ref')
    if not ref:
        return _MacroInfo(None, None, None)
    owner_short = ref_sources.get('component/@ref', 'core')
    if owner_short == 'core':
        pkg_root = root
    else:
        pkg_root = root / 'extensions' / f'ego_dlc_{owner_short}'
        if not pkg_root.is_dir():
            pkg_root = root
    path = resolve_macro_path(root, pkg_root, ref, kind='weapons')
    element_loaded = _load_macro(path)
    return _MacroInfo(element=element_loaded, path=path,
                      subtype=_macro_subtype(path))


def _load_macro(path: Optional[Path]) -> Optional[ET.Element]:
    if path is None:
        return None
    try:
        return ET.parse(path).getroot().find('macro')
    except (FileNotFoundError, ET.ParseError):
        return None


def _macro_subtype(macro_path: Optional[Path]) -> Optional[str]:
    """Return the subtype dir name (e.g. 'standard', 'energy', 'boron').

    Weapon macros live under `.../weaponsystems/<subtype>/macros/*.xml` (both
    core PascalCase and DLC lowercase). Returns the parent-of-'macros' dir.
    """
    if macro_path is None:
        return None
    parts = list(macro_path.parts)
    try:
        idx = parts.index('macros')
    except ValueError:
        return None
    if idx == 0:
        return None
    return parts[idx - 1]


def _display_name(rec, primary: _MacroInfo, fallback: Optional[_MacroInfo],
                  loc_primary: Locale, loc_fallback: Locale) -> str:
    """Resolve display name from macro's identification/@name, fall back to
    the other side's macro, then to the wares.xml @name attr, then to the
    ware id.
    """
    tried: list[tuple[_MacroInfo, Locale]] = []
    if primary is not None:
        tried.append((primary, loc_primary))
    if fallback is not None:
        tried.append((fallback, loc_fallback))
    for info, loc in tried:
        m = info.element if info is not None else None
        if m is None:
            continue
        ident = m.find('properties/identification')
        if ident is not None:
            val = resolve_attr_ref(ident, loc, attr='name',
                                    fallback=m.get('name'))
            if val:
                return val
    # Wares.xml also carries @name on the ware — use it if present.
    element = getattr(rec, 'new', None) or getattr(rec, 'old', None) \
              or getattr(rec, 'element', None)
    if element is not None:
        val = resolve_attr_ref(element, loc_primary, attr='name',
                                fallback=rec.key)
        if val:
            return val
    return rec.key


def _classify(ware_id: str, ware_element: Optional[ET.Element],
              macro_info: Optional[_MacroInfo]) -> list[str]:
    """Return `[subtype, ...tag_tokens]`.

    subtype: macro-path parent-dir name (e.g. 'standard', 'energy', 'heavy',
    'capital', 'boron', 'highpower'). For mines (macro class="mine" OR id
    matching `^weapon_.*_mine_`), prepend 'mine'. Source-of-truth for subtype
    is the on-disk dir; ware id alone doesn't encode it.

    tag_tokens: ware `tags`, minus the generic set (`weapon`, `component`)
    and minus `deprecated` (surfaced via the DEPRECATED headline, not a
    classification).
    """
    is_mine = False
    if macro_info is not None and macro_info.element is not None:
        if macro_info.element.get('class') == 'mine':
            is_mine = True
    if _MINE_ID_RE.match(ware_id):
        is_mine = True

    subtype: Optional[str] = macro_info.subtype if macro_info is not None else None

    tokens: list[str] = []
    if is_mine:
        tokens.append('mine')
    if subtype:
        tokens.append(subtype)

    if ware_element is not None:
        raw_tags = (ware_element.get('tags') or '').split()
        for t in raw_tags:
            if t in GENERIC_CLASSIFICATION_TOKENS:
                continue
            if t == 'deprecated':
                continue
            tokens.append(t)

    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _format(name: str, classifications: list[str], srcs: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src_label = f' {srcs}' if srcs else ''
    return f'[{TAG}] {name}{cls}{src_label}: {", ".join(parts)}'
