"""Loadouts rule: emit outputs for loadouts + loadoutrules changes.

Two sub-sources share the `loadouts` tag, distinguished by `extras.subsource`:

- `loadout` — `diff_library` on `libraries/loadouts.xml`. Simple `@id`-keyed
  entity model. Display name comes from the loadout's `@macro` → the ship
  macro's `<identification @name>` → locale page 20101; falls back to the
  loadout `@id`. Fields diffed: equipment macro refs (engine/shield/turret/
  weapon/ammunition), software wares, virtualmacros.
- `rule` — `libraries/loadoutrules.xml`. The rule's own `@id` isn't stable —
  keys compose applicability attrs instead:
  `(container, ruleset_type, category, mk,
    tuple(sorted(classes)), tuple(sorted(purposes)),
    tuple(sorted(factiontags)), tuple(sorted(cargotags)))`.
  `tuple(sorted(...))` — NOT `frozenset` — keeps `repr(entity_key)` stable
  for Tier B snapshots.

**Multiset handling on the rule sub-source.** If multiple rules share the
same composite applicability tuple on ONE side, naive `dict`-keyed paired
diff loses the multiset: `diff_library`'s `_index_by_key` overwrites earlier
with later. To preserve multiset semantics:

1. Run `diff_library` with `key_fn=lambda e: id(e)` so every rule gets a
   unique key (no merge). Use the returned `effective_old_root` /
   `effective_new_root` as the materialized trees; discard the added/
   removed/modified lists.
2. Group rules by composite applicability tuple per side.
3. For each applicability key:
   - Unique on both sides (one old, one new) → regular paired diff. Emit
     `modified` only if the signatures differ; `added` / `removed` when
     one side is absent.
   - Multiset on either side → bag-diff signatures; emit `added` per
     new-only signature and `removed` per old-only. NO `modified` output
     (multiset prevents cascade false positives when rules shift within
     a bucket).
"""
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.paths import resolve_macro_path
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'loadouts'
LOCALE_PAGE = 20101

# Tokens that appear on every loadout/rule output and carry no signal.
_GENERIC_FILTER = frozenset({'loadout', 'rule'})

# Applicability axes that must NOT be listed as diffed rule attrs.
_RULE_APPLICABILITY_ATTRS = frozenset({
    'category', 'mk', 'classes', 'purposes', 'factiontags', 'cargotags',
})


# ---------- top-level entry ----------


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit loadouts rule outputs for old_root → new_root."""
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    # --- Loadout sub-source ---
    loadout_report = diff_library(
        old_root, new_root, 'libraries/loadouts.xml', './/loadout',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='loadouts_loadout',
    )
    outputs.extend(_emit_loadout_subsource(
        old_root, new_root, loadout_report, loc_old, loc_new,
    ))

    # --- Rule sub-source ---
    # `key_fn=id(elem)` gives each rule a unique key so `_index_by_key`
    # doesn't dedupe. We consume `effective_old_root` / `effective_new_root`
    # and re-index manually with the composite applicability tuple.
    rule_report = diff_library(
        old_root, new_root, 'libraries/loadoutrules.xml', './/rule',
        key_fn=lambda e: id(e),
        key_fn_identity='loadouts_rule_identity',
    )
    outputs.extend(_emit_rule_subsource(rule_report))

    forward_incomplete_many(
        [
            (_PrefixedReport(loadout_report, 'loadout'), 'loadout'),
            (_PrefixedReport(rule_report, 'rule'), 'rule'),
        ],
        outputs, tag=TAG,
    )
    forward_warnings(loadout_report.warnings, outputs, tag=TAG)
    forward_warnings(rule_report.warnings, outputs, tag=TAG)
    return outputs


# ---------- DiffReport wrapper (propagates subsource tuple keys) ----------


class _PrefixedReport:
    """Wraps a DiffReport so `forward_incomplete` sees tuple entity_keys.

    The rule uses `(subsource, inner_key)` tuple entity_keys. Underlying
    DiffReport.failures carry raw ids (or whatever `_infer_affected_keys`
    extracted) in `affected_keys` — this wrapper rewrites them to the
    subsource-prefixed tuples used by the rule's outputs so contamination
    actually matches.
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


# ---------- loadout sub-source ----------


def _emit_loadout_subsource(old_root: Path, new_root: Path, report,
                            loc_old: Locale, loc_new: Locale
                            ) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.extend(_emit_loadout_added(new_root, rec, loc_new))
    for rec in report.removed:
        outputs.extend(_emit_loadout_removed(old_root, rec, loc_old))
    for rec in report.modified:
        outputs.extend(_emit_loadout_modified(
            old_root, new_root, rec, loc_old, loc_new,
        ))
    return outputs


def _emit_loadout_added(new_root: Path, rec, loc_new: Locale) -> list[RuleOutput]:
    loadout = rec.element
    loadout_id = rec.key
    macro_ref = loadout.get('macro')
    name = _loadout_display_name(new_root, loadout, loc_new, loadout_id)
    srcs = render_sources(None, rec.sources)
    text = _format(name, ['loadout'], srcs, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('loadout', loadout_id),
        'kind': 'added',
        'subsource': 'loadout',
        'classifications': ['loadout'],
        'loadout_id': loadout_id,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
        'refs': {'ship_macro': macro_ref} if macro_ref else {},
    })]


def _emit_loadout_removed(old_root: Path, rec, loc_old: Locale) -> list[RuleOutput]:
    loadout = rec.element
    loadout_id = rec.key
    macro_ref = loadout.get('macro')
    name = _loadout_display_name(old_root, loadout, loc_old, loadout_id)
    srcs = render_sources(rec.sources, None)
    text = _format(name, ['loadout'], srcs, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('loadout', loadout_id),
        'kind': 'removed',
        'subsource': 'loadout',
        'classifications': ['loadout'],
        'loadout_id': loadout_id,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
        'refs': {'ship_macro': macro_ref} if macro_ref else {},
    })]


def _emit_loadout_modified(old_root: Path, new_root: Path, rec,
                           loc_old: Locale, loc_new: Locale) -> list[RuleOutput]:
    loadout_id = rec.key
    new_macro = rec.new.get('macro')
    name = _loadout_display_name(new_root, rec.new, loc_new, loadout_id)
    if name == loadout_id:
        name = _loadout_display_name(old_root, rec.old, loc_old, loadout_id)

    changes = _loadout_field_diff(rec.old, rec.new)
    if not changes:
        return []

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, ['loadout'], srcs, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('loadout', loadout_id),
        'kind': 'modified',
        'subsource': 'loadout',
        'classifications': ['loadout'],
        'loadout_id': loadout_id,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
        'refs': {'ship_macro': new_macro} if new_macro else {},
    })]


def _loadout_display_name(root: Path, loadout: ET.Element,
                          loc: Locale, fallback: str) -> str:
    """Resolve via loadout @macro → ship macro file's identification @name.

    Falls back to the loadout @id when macro resolution fails. Loadouts can
    reference macros whose on-disk files may or may not live in core vs DLC;
    `resolve_macro_path(kind='ships')` handles both.
    """
    macro_ref = loadout.get('macro')
    if not macro_ref:
        return fallback
    path = resolve_macro_path(root, root, macro_ref, kind='ships')
    if path is None:
        return fallback
    try:
        tree_root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return fallback
    macro = tree_root.find('macro') if tree_root.tag == 'macros' else (
        tree_root if tree_root.tag == 'macro' else None
    )
    if macro is None:
        return fallback
    ident = macro.find('properties/identification')
    if ident is None:
        return fallback
    resolved = resolve_attr_ref(ident, loc, attr='name', fallback='')
    return resolved or fallback


def _loadout_field_diff(old_el: ET.Element, new_el: ET.Element) -> list[str]:
    """Summarize equipment slot + software + virtualmacros + ammunition deltas."""
    changes: list[str] = []

    # Macro @ref itself (the target ship macro).
    if old_el.get('macro') != new_el.get('macro'):
        changes.append(f"ship_macro {old_el.get('macro')}→{new_el.get('macro')}")

    # Equipment slots: engine / shield / turret / weapon under <macros>.
    for slot in ('engine', 'shield', 'turret', 'weapon'):
        ov = _slot_bag(old_el, slot)
        nv = _slot_bag(new_el, slot)
        if ov != nv:
            added = sorted(_fmt_slot_item(p) for p in (nv - ov))
            removed = sorted(_fmt_slot_item(p) for p in (ov - nv))
            parts = []
            if added:
                parts.append('added={' + ','.join(added) + '}')
            if removed:
                parts.append('removed={' + ','.join(removed) + '}')
            changes.append(f"{slot} " + ' '.join(parts))

    # Software wares.
    ov = _software_set(old_el)
    nv = _software_set(new_el)
    if ov != nv:
        added = sorted(nv - ov)
        removed = sorted(ov - nv)
        parts = []
        if added:
            parts.append('added={' + ','.join(added) + '}')
        if removed:
            parts.append('removed={' + ','.join(removed) + '}')
        changes.append('software ' + ' '.join(parts))

    # Virtualmacros (thruster, etc.).
    ov = _virtualmacro_bag(old_el)
    nv = _virtualmacro_bag(new_el)
    if ov != nv:
        added = sorted(nv - ov)
        removed = sorted(ov - nv)
        parts = []
        if added:
            parts.append('added={' + ','.join(added) + '}')
        if removed:
            parts.append('removed={' + ','.join(removed) + '}')
        changes.append('virtualmacros ' + ' '.join(parts))

    # Ammunition counts.
    ov = _ammunition_map(old_el)
    nv = _ammunition_map(new_el)
    if ov != nv:
        ammo_parts: list[str] = []
        for key in sorted(set(ov) | set(nv)):
            if ov.get(key) != nv.get(key):
                ammo_parts.append(f'{key}:{ov.get(key)}→{nv.get(key)}')
        if ammo_parts:
            changes.append('ammunition ' + ', '.join(ammo_parts))

    return changes


def _fmt_slot_item(item: tuple[str, str]) -> str:
    macro, path = item
    return f'{macro}@{path}'


def _slot_bag(loadout: ET.Element, slot: str) -> frozenset:
    """Return a frozenset of (macro, path) tuples for one equipment slot.

    Each loadout's slot has a per-child `path` attr that's unique within the
    loadout, so (macro, path) is a natural identity. If a child is missing
    a path, its enumeration index provides a stable fallback so repeated
    path-less entries still produce distinct bag members.
    """
    macros_el = loadout.find('macros')
    if macros_el is None:
        return frozenset()
    items = []
    for idx, child in enumerate(macros_el.findall(slot)):
        macro = child.get('macro') or ''
        path = child.get('path') or f'#{idx}'
        items.append((macro, path))
    return frozenset(items)


def _software_set(loadout: ET.Element) -> frozenset:
    out = set()
    sw = loadout.find('software')
    if sw is None:
        return frozenset()
    for s in sw.findall('software'):
        w = s.get('ware')
        if w:
            out.add(w)
    return frozenset(out)


def _virtualmacro_bag(loadout: ET.Element) -> frozenset:
    out = set()
    vm = loadout.find('virtualmacros')
    if vm is None:
        return frozenset()
    for child in vm:
        macro = child.get('macro') or ''
        out.add(f'{child.tag}:{macro}')
    return frozenset(out)


def _ammunition_map(loadout: ET.Element) -> dict[str, str]:
    """Return {macro: count_attrs_joined_by_comma}."""
    out: dict[str, str] = {}
    ammo = loadout.find('ammunition')
    if ammo is None:
        return out
    for a in ammo.findall('ammunition'):
        macro = a.get('macro') or ''
        if not macro:
            continue
        parts = []
        for attr in ('exact', 'min', 'max'):
            v = a.get(attr)
            if v is not None:
                parts.append(f'{attr}={v}')
        out[macro] = ','.join(parts) if parts else ''
    return out


# ---------- rule sub-source ----------


def _emit_rule_subsource(rule_report) -> list[RuleOutput]:
    """Build composite-applicability buckets on each side and emit diffs.

    The `rule_report` was materialized with unique `id(elem)` keys, so we
    discard its added/removed/modified lists and re-index the effective
    trees manually.
    """
    old_tree = rule_report.effective_old_root
    new_tree = rule_report.effective_new_root
    if old_tree is None and new_tree is None:
        return []

    # ElementTree has no parent pointer; the composite-key builder needs to
    # walk up. Build a parent map per effective tree once per run.
    parent_maps: dict[int, dict[int, ET.Element]] = {}
    if old_tree is not None:
        parent_maps[id(old_tree)] = _build_parent_map(old_tree)
    if new_tree is not None:
        parent_maps[id(new_tree)] = _build_parent_map(new_tree)

    old_buckets = _bucket_rules(old_tree, parent_maps)
    new_buckets = _bucket_rules(new_tree, parent_maps)

    outputs: list[RuleOutput] = []
    keys = set(old_buckets) | set(new_buckets)
    for composite_key in sorted(keys, key=_key_sort_tuple):
        old_rules = old_buckets.get(composite_key, [])
        new_rules = new_buckets.get(composite_key, [])
        outputs.extend(_emit_bucket(composite_key, old_rules, new_rules))
    return outputs


def _build_parent_map(tree_root: ET.Element) -> dict[int, ET.Element]:
    return {id(c): p for p in tree_root.iter() for c in p}


def _bucket_rules(tree_root: Optional[ET.Element],
                  parent_maps: dict[int, dict[int, ET.Element]]
                  ) -> dict[tuple, list[ET.Element]]:
    """Group all `<rule>` elements by composite applicability key."""
    buckets: dict[tuple, list[ET.Element]] = {}
    if tree_root is None:
        return buckets
    pmap = parent_maps.get(id(tree_root), {})
    for rule in tree_root.iter('rule'):
        key = _rule_composite_key(rule, pmap)
        if key is None:
            continue
        buckets.setdefault(key, []).append(rule)
    return buckets


def _rule_composite_key(rule: ET.Element,
                        pmap: dict[int, ET.Element]) -> Optional[tuple]:
    """Build the composite applicability tuple for one `<rule>`.

    Returns None if the enclosing structure doesn't have a container
    (`unit`/`deployable`) or a `<ruleset @type>` — those rules can't be
    keyed stably.
    """
    container = _find_ancestor_tag(rule, pmap, ('unit', 'deployable'))
    ruleset_type = _find_ancestor_attr(rule, pmap, 'ruleset', 'type')
    if container is None or ruleset_type is None:
        return None
    category = rule.get('category') or ''
    mk = rule.get('mk') or ''
    classes = tuple(sorted((rule.get('classes') or '').split()))
    purposes = tuple(sorted((rule.get('purposes') or '').split()))
    factiontags = tuple(sorted((rule.get('factiontags') or '').split()))
    cargotags = tuple(sorted((rule.get('cargotags') or '').split()))
    return (
        container, ruleset_type, category, mk,
        classes, purposes, factiontags, cargotags,
    )


def _find_ancestor_tag(elem: ET.Element,
                       pmap: dict[int, ET.Element],
                       match_tags: tuple[str, ...]) -> Optional[str]:
    cur = elem
    seen: set[int] = set()
    while id(cur) in pmap and id(cur) not in seen:
        seen.add(id(cur))
        parent = pmap[id(cur)]
        if parent.tag in match_tags:
            return parent.tag
        cur = parent
    return None


def _find_ancestor_attr(elem: ET.Element,
                        pmap: dict[int, ET.Element],
                        match_tag: str, attr: str) -> Optional[str]:
    cur = elem
    seen: set[int] = set()
    while id(cur) in pmap and id(cur) not in seen:
        seen.add(id(cur))
        parent = pmap[id(cur)]
        if parent.tag == match_tag:
            return parent.get(attr)
        cur = parent
    return None


def _key_sort_tuple(k: tuple) -> tuple:
    """Safe sort key — every element is a string or a tuple of strings."""
    out = []
    for el in k:
        if isinstance(el, tuple):
            out.append(tuple(str(x) for x in el))
        else:
            out.append(str(el))
    return tuple(out)


def _emit_bucket(composite_key: tuple,
                 old_rules: list[ET.Element],
                 new_rules: list[ET.Element]) -> list[RuleOutput]:
    """Emit diffs for one applicability bucket.

    Branches:
    - Both empty: impossible (caller filtered).
    - Old empty → emit `added` per new rule.
    - New empty → emit `removed` per old rule.
    - Both len==1 → paired diff (modified if sigs differ).
    - Multiset (either side >1) → bag-diff; emit adds for new-only and
      removes for old-only sigs. NO `modified`.
    """
    outputs: list[RuleOutput] = []
    old_count = len(old_rules)
    new_count = len(new_rules)

    if old_count == 0 and new_count == 0:
        return outputs

    if old_count == 0:
        for r in new_rules:
            outputs.append(_emit_rule_added(composite_key, r))
        return outputs

    if new_count == 0:
        for r in old_rules:
            outputs.append(_emit_rule_removed(composite_key, r))
        return outputs

    if old_count == 1 and new_count == 1:
        out = _emit_rule_modified(composite_key, old_rules[0], new_rules[0])
        if out is not None:
            outputs.append(out)
        return outputs

    # Multiset path: count sigs and emit adds/removes for the delta.
    return _emit_bucket_multiset(composite_key, old_rules, new_rules)


def _emit_bucket_multiset(composite_key: tuple,
                          old_rules: list[ET.Element],
                          new_rules: list[ET.Element]) -> list[RuleOutput]:
    """Multiset diff within one applicability bucket.

    For each signature, count old vs new occurrences. Pair up `min(count)`
    as unchanged (no output). Any excess on the new side → `added`; excess
    on the old side → `removed`. We preserve the original rule-element order
    when emitting, so snapshots stay deterministic.
    """
    outputs: list[RuleOutput] = []
    old_sigs = [_rule_signature(r) for r in old_rules]
    new_sigs = [_rule_signature(r) for r in new_rules]

    old_counts: dict[tuple, int] = {}
    new_counts: dict[tuple, int] = {}
    for sig in old_sigs:
        old_counts[sig] = old_counts.get(sig, 0) + 1
    for sig in new_sigs:
        new_counts[sig] = new_counts.get(sig, 0) + 1

    # Walk new rules; emit adds for instances beyond what old had.
    emitted: dict[tuple, int] = {}
    for sig, rule_el in zip(new_sigs, new_rules):
        pairs_used = emitted.get(sig, 0)
        old_has = old_counts.get(sig, 0)
        if pairs_used < old_has:
            emitted[sig] = pairs_used + 1  # pair with old; unchanged.
        else:
            outputs.append(_emit_rule_added(composite_key, rule_el,
                                            multiset=True))

    # Walk old rules; emit removes for instances beyond what new has.
    emitted = {}
    for sig, rule_el in zip(old_sigs, old_rules):
        pairs_used = emitted.get(sig, 0)
        new_has = new_counts.get(sig, 0)
        if pairs_used < new_has:
            emitted[sig] = pairs_used + 1
        else:
            outputs.append(_emit_rule_removed(composite_key, rule_el,
                                              multiset=True))
    return outputs


def _rule_signature(rule: ET.Element) -> tuple:
    """Hashable signature of a rule's diffed attrs (excludes applicability).

    Equal signatures represent interchangeable rules within the same
    applicability bucket.
    """
    items = []
    for k, v in rule.attrib.items():
        if k in _RULE_APPLICABILITY_ATTRS:
            continue
        items.append((k, v))
    return tuple(sorted(items))


def _rule_display_name(composite_key: tuple) -> str:
    container, ruleset_type, category, mk, *_ = composite_key
    return f'{container}/{ruleset_type}/{category}/mk{mk}'


def _rule_classifications(composite_key: tuple) -> list[str]:
    container, ruleset_type, category, mk, classes, purposes, \
        factiontags, _cargotags = composite_key
    tokens = ['rule', container, ruleset_type]
    if category:
        tokens.append(category)
    if mk:
        tokens.append(f'mk{mk}')
    for t in classes:
        tokens.append(t)
    for t in purposes:
        tokens.append(t)
    for t in factiontags:
        tokens.append(t)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in _GENERIC_FILTER or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _rule_refs(composite_key: tuple) -> dict:
    container, ruleset_type, category, mk, classes, purposes, \
        factiontags, cargotags = composite_key
    return {
        'applicability': {
            'container': container,
            'ruleset_type': ruleset_type,
            'category': category,
            'mk': mk,
            'classes': list(classes),
            'purposes': list(purposes),
            'factiontags': list(factiontags),
            'cargotags': list(cargotags),
        },
    }


def _emit_rule_added(composite_key: tuple, rule_el: ET.Element,
                     multiset: bool = False) -> RuleOutput:
    name = _rule_display_name(composite_key)
    classifications = _rule_classifications(composite_key)
    sig_parts = _format_rule_attrs(rule_el)
    srcs = render_sources(None, ['core'])
    parts = ['NEW']
    if sig_parts:
        parts.append(sig_parts)
    text = _format(name, classifications, srcs, parts)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('rule', composite_key),
        'kind': 'added',
        'subsource': 'rule',
        'classifications': classifications,
        'rule_signature': _rule_signature(rule_el),
        'multiset': multiset,
        'refs': _rule_refs(composite_key),
    })


def _emit_rule_removed(composite_key: tuple, rule_el: ET.Element,
                       multiset: bool = False) -> RuleOutput:
    name = _rule_display_name(composite_key)
    classifications = _rule_classifications(composite_key)
    sig_parts = _format_rule_attrs(rule_el)
    srcs = render_sources(['core'], None)
    parts = ['REMOVED']
    if sig_parts:
        parts.append(sig_parts)
    text = _format(name, classifications, srcs, parts)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('rule', composite_key),
        'kind': 'removed',
        'subsource': 'rule',
        'classifications': classifications,
        'rule_signature': _rule_signature(rule_el),
        'multiset': multiset,
        'refs': _rule_refs(composite_key),
    })


def _emit_rule_modified(composite_key: tuple,
                        old_el: ET.Element, new_el: ET.Element
                        ) -> Optional[RuleOutput]:
    old_sig = _rule_signature(old_el)
    new_sig = _rule_signature(new_el)
    if old_sig == new_sig:
        return None
    name = _rule_display_name(composite_key)
    classifications = _rule_classifications(composite_key)

    changes = _rule_attr_diff(old_el, new_el)
    srcs = render_sources(['core'], ['core'])
    text = _format(name, classifications, srcs, changes)
    return RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('rule', composite_key),
        'kind': 'modified',
        'subsource': 'rule',
        'classifications': classifications,
        'old_rule_signature': old_sig,
        'new_rule_signature': new_sig,
        'refs': _rule_refs(composite_key),
    })


def _rule_attr_diff(old_el: ET.Element, new_el: ET.Element) -> list[str]:
    """Label changes between two rules sharing the same applicability key.

    Only diffs the non-applicability attrs.
    """
    out = []
    all_attrs = sorted(set(old_el.attrib) | set(new_el.attrib))
    for attr in all_attrs:
        if attr in _RULE_APPLICABILITY_ATTRS:
            continue
        ov = old_el.get(attr)
        nv = new_el.get(attr)
        if ov != nv:
            out.append(f'{attr} {ov}→{nv}')
    return out


def _format_rule_attrs(rule_el: ET.Element) -> str:
    """Compact key=val list of non-applicability attrs for add/remove rows."""
    parts = []
    for attr in sorted(rule_el.attrib):
        if attr in _RULE_APPLICABILITY_ATTRS:
            continue
        parts.append(f'{attr}={rule_el.get(attr)}')
    return ' '.join(parts)


# ---------- formatting ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'
