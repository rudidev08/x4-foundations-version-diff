"""Factions rule: emit outputs for faction + diplomacy-action changes.

Two sub-sources share the `factions` tag, distinguished by `extras.subsource`:

- `faction` — `diff_library` on `libraries/factions.xml`. Keyed by `@id`,
  display name via `resolve_attr_ref(faction, locale, attr='name')`. Fields:
  top-level attrs, `<licences>` entries keyed by composite `(type, factions)`
  (real X4 data has multiple same-@type licences distinguished by
  `@factions`; single-key `@type` mispairs them), `<relations>` default
  relations. Parse-time uniqueness: duplicate `(type, factions)` composite
  within one faction's `<licences>` block is emitted as an incomplete with
  reason `'licence_type_not_unique'`.
- `action` — `diff_library` on `libraries/diplomacy.xml`. Keyed by `@id`,
  display name via `resolve_attr_ref`. Each action is diffed as a full
  subtree; child tags route through an explicit matcher table:
  - `<cost>` / `<reward>` → keyed by `@ware` when present; multiset by
    canonical attribute signature otherwise.
  - `<params>/<param>` and `<param>/<input_param>` → keyed by `@name`
    with a uniqueness assertion (reason `'param_name_not_unique'`).
  - `<time>`, `<icon>`, `<success>`, `<failure>`, `<agent>` → singleton;
    attrs diffed directly.
  - Any unenumerated direct child → incomplete with reason
    `'no_child_matcher'`. There is no generic recursion fallback; the
    explicit table keeps the diff semantic rather than syntactic.

Both sub-sources route failures through `forward_incomplete_many` with
per-subsource scoping so a faction parse error cannot contaminate action
rows and vice versa.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'factions'

# Faction top-level attrs diffed directly.
FACTION_ATTRS = ('behaviourset', 'primaryrace', 'policefaction')

# Action top-level attrs diffed directly.
ACTION_ATTRS = ('category', 'unique', 'hidden', 'friendgroup',
                'shortdescription', 'description')

# Action singleton-child tags: attrs diffed in place.
ACTION_SINGLETONS = ('time', 'icon', 'success', 'failure', 'agent')

# Action child tags the rule knows how to diff. Anything else is an
# incomplete with reason `'no_child_matcher'`.
ACTION_KNOWN_CHILDREN = frozenset({'cost', 'reward', 'params'} | set(ACTION_SINGLETONS))

# Classifications generic filter (see spec 3.1).
_GENERIC_FILTER = frozenset({'faction', 'action'})


# ---------- synthetic sub-reports for rule-level assertions ----------


@dataclass
class _RuleReport:
    """Synthetic DiffReport-shaped wrapper for rule-level diagnostics.

    `diff_library` only emits failures for DLC patch errors / parse errors;
    rule-level assertions (duplicate licence @type, duplicate param @name,
    unhandled action child tag) live in a parallel bag that rides the same
    `forward_incomplete_many` pipeline so the subsource scope stays sane.
    """
    failures: list[tuple[str, dict]] = field(default_factory=list)
    warnings: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


@dataclass
class _MergedReport:
    """Merge a DiffReport with a _RuleReport for a single `forward_incomplete`
    scope. Prefixes `affected_keys` from the underlying DiffReport's failures
    with the subsource tag so `entity_key=(subsource, id)` matches.
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
    """Emit factions rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; the rule is library-driven.
    """
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    faction_report = diff_library(
        old_root, new_root, 'libraries/factions.xml', './/faction',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='factions_faction',
    )
    faction_rule_report = _RuleReport()
    outputs.extend(_emit_faction(faction_report, loc_old, loc_new,
                                 faction_rule_report))
    # Parse-time uniqueness: scan every faction in both effective trees
    # regardless of diff status. Unchanged factions still need the
    # assertion to flag malformed data.
    _scan_faction_uniqueness(faction_report, faction_rule_report)

    action_report = diff_library(
        old_root, new_root, 'libraries/diplomacy.xml', './/action',
        key_fn=lambda e: e.get('id'),
        key_fn_identity='factions_action',
    )
    action_rule_report = _RuleReport()
    outputs.extend(_emit_action(action_report, loc_old, loc_new,
                                action_rule_report))
    _scan_action_uniqueness(action_report, action_rule_report)

    # Mark any output whose entity_key is in a rule_report failure's
    # affected_keys as incomplete. `forward_incomplete_many` does this for
    # the wrapped failures via `_MergedReport.failures` below.
    forward_incomplete_many(
        [
            (_MergedReport(faction_report, 'faction', faction_rule_report),
             'faction'),
            (_MergedReport(action_report, 'action', action_rule_report),
             'action'),
        ],
        outputs, tag=TAG,
    )
    forward_warnings(faction_report.warnings, outputs, tag=TAG)
    forward_warnings(action_report.warnings, outputs, tag=TAG)
    return outputs


# ---------- faction sub-source ----------


def _emit_faction(report, loc_old: Locale, loc_new: Locale,
                  rule_report: _RuleReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.extend(_emit_faction_added(rec, loc_new, rule_report))
    for rec in report.removed:
        outputs.extend(_emit_faction_removed(rec, loc_old, rule_report))
    for rec in report.modified:
        outputs.extend(_emit_faction_modified(rec, loc_old, loc_new,
                                              rule_report))
    return outputs


def _emit_faction_added(rec, loc_new: Locale,
                        rule_report: _RuleReport) -> list[RuleOutput]:
    faction = rec.element
    fid = rec.key
    name = resolve_attr_ref(faction, loc_new, attr='name', fallback=fid)
    classifications = _faction_classifications(faction)
    srcs = render_sources(None, rec.sources)
    text = _format(name, classifications, srcs, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('faction', fid),
        'kind': 'added',
        'subsource': 'faction',
        'classifications': classifications,
        'faction_id': fid,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_faction_removed(rec, loc_old: Locale,
                          rule_report: _RuleReport) -> list[RuleOutput]:
    faction = rec.element
    fid = rec.key
    name = resolve_attr_ref(faction, loc_old, attr='name', fallback=fid)
    classifications = _faction_classifications(faction)
    srcs = render_sources(rec.sources, None)
    text = _format(name, classifications, srcs, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('faction', fid),
        'kind': 'removed',
        'subsource': 'faction',
        'classifications': classifications,
        'faction_id': fid,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_faction_modified(rec, loc_old: Locale, loc_new: Locale,
                           rule_report: _RuleReport) -> list[RuleOutput]:
    fid = rec.key
    name = resolve_attr_ref(rec.new, loc_new, attr='name', fallback=fid)
    if name == fid:
        name = resolve_attr_ref(rec.old, loc_old, attr='name', fallback=fid)
    classifications = _faction_classifications(rec.new)

    changes: list[str] = []
    # Top-level attrs.
    for a in FACTION_ATTRS:
        ov = rec.old.get(a)
        nv = rec.new.get(a)
        if ov != nv:
            changes.append(f'{a} {ov}→{nv}')
    # Licences (keyed by @type).
    changes.extend(_diff_licences(rec.old, rec.new))
    # Default relations (keyed by @faction).
    changes.extend(_diff_relations(rec.old, rec.new))

    if not changes:
        return []

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, srcs, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('faction', fid),
        'kind': 'modified',
        'subsource': 'faction',
        'classifications': classifications,
        'faction_id': fid,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]


def _faction_classifications(faction: ET.Element) -> list[str]:
    """Return [primaryrace, behaviourset] minus the generic filter.

    Spec says `["faction", @primaryrace, @behaviourset]` — the literal
    `faction` token is a generic classification discarded by the filter.
    """
    out: list[str] = ['faction']
    pr = faction.get('primaryrace')
    if pr:
        out.append(pr)
    bs = faction.get('behaviourset')
    if bs:
        out.append(bs)
    return [t for t in out if t not in _GENERIC_FILTER]


def _scan_faction_uniqueness(report, rule_report: _RuleReport) -> None:
    """Walk both effective trees, checking licence @type uniqueness per
    faction. De-dup (fid, type) pairs so each dup only generates one
    failure regardless of which side it appears on.
    """
    seen: set[tuple[str, str]] = set()
    for root in (report.effective_old_root, report.effective_new_root):
        if root is None:
            continue
        for faction in root.iter('faction'):
            fid = faction.get('id')
            if fid is None:
                continue
            _check_licence_uniqueness(faction, fid, rule_report, seen)


def _scan_action_uniqueness(report, rule_report: _RuleReport) -> None:
    """Walk both effective trees, checking action child tags and param
    @name uniqueness per action. De-dup emitted failures across both sides
    so we don't double-report when the same malformed action exists in
    both TEST-1.00 and TEST-2.00.
    """
    seen_child: set[tuple[str, str]] = set()
    seen_param: set[tuple[str, str, str]] = set()
    for root in (report.effective_old_root, report.effective_new_root):
        if root is None:
            continue
        for action in root.iter('action'):
            aid = action.get('id')
            if aid is None:
                continue
            _check_action_children(action, aid, rule_report, seen_child)
            _check_params_uniqueness(action, aid, rule_report, seen_param)


def _licence_key(lic: ET.Element) -> tuple:
    """Licence composite key.

    Spec called for keying by `@type`, but real X4 data has multiple
    `<licence @type="capitalequipment">` entries per faction, distinguished
    by `@factions` (the whitelist of factions the licence is granted
    toward). Composite key `(type, factions)` is unique across every
    faction's licences block in both 8.00H4 and 9.00B6. The rule treats
    that composite as the primary key; the uniqueness assertion fires when
    the composite duplicates.
    """
    return (lic.get('type'), lic.get('factions'))


def _check_licence_uniqueness(faction: Optional[ET.Element], fid: str,
                              rule_report: _RuleReport,
                              dedup: Optional[set] = None) -> None:
    """Duplicate `(type, factions)` composite within a single <licences>
    block is a parse-time incomplete (reason `licence_type_not_unique`).

    `dedup`, when provided, is a shared set of (fid, composite_key) tuples
    that already produced a failure — the scanner passes one across both
    effective trees so unchanged malformed factions don't double-report.
    """
    if faction is None:
        return
    licences = faction.find('licences')
    if licences is None:
        return
    occurrences: dict[tuple, int] = {}
    for lic in licences.findall('licence'):
        k = _licence_key(lic)
        if k[0] is None:
            continue
        occurrences[k] = occurrences.get(k, 0) + 1
    dupes = sorted([k for k, c in occurrences.items() if c > 1])
    for k in dupes:
        lic_type, lic_factions = k
        dkey = (fid, lic_type, lic_factions)
        if dedup is not None:
            if dkey in dedup:
                continue
            dedup.add(dkey)
        label = f'@type={lic_type}'
        if lic_factions is not None:
            label += f' @factions={lic_factions}'
        rule_report.failures.append((
            f'faction {fid} has duplicate licence {label}',
            {
                'reason': 'licence_type_not_unique',
                'faction_id': fid,
                'licence_type': lic_type,
                'licence_factions': lic_factions,
                'affected_keys': [('faction', fid)],
            },
        ))


def _diff_licences(old_faction: ET.Element, new_faction: ET.Element) -> list[str]:
    """Diff <licences>/<licence> entries keyed by composite `(type, factions)`.

    Real X4 factions have multiple same-@type licences distinguished by
    `@factions`; keying on both avoids mispairing. Adds/removes/field-
    changes surface per-licence labels. If the composite still collides
    (genuinely malformed data), the uniqueness check emits the incomplete.
    """
    old_map = _first_by_key(old_faction.find('licences'),
                            'licence', _licence_key)
    new_map = _first_by_key(new_faction.find('licences'),
                            'licence', _licence_key)
    out: list[str] = []
    for key in sorted(new_map.keys() - old_map.keys(), key=_key_repr):
        out.append(f'licence[{_key_repr(key)}] added')
    for key in sorted(old_map.keys() - new_map.keys(), key=_key_repr):
        out.append(f'licence[{_key_repr(key)}] removed')
    for key in sorted(old_map.keys() & new_map.keys(), key=_key_repr):
        ov = old_map[key].attrib
        nv = new_map[key].attrib
        for a in sorted(set(ov) | set(nv)):
            if ov.get(a) != nv.get(a):
                out.append(f'licence[{_key_repr(key)}] {a} '
                           f'{ov.get(a)}→{nv.get(a)}')
    return out


def _key_repr(key: tuple) -> str:
    """Render a (type, factions) composite key as `type=T` or
    `type=T,factions=F` for diff labels."""
    t, f = key
    if f is None:
        return f'type={t}'
    return f'type={t},factions={f}'


def _diff_relations(old_faction: ET.Element, new_faction: ET.Element) -> list[str]:
    """Diff <relations>/<relation> default-relation entries keyed by @faction."""
    old_map = _first_by_key(old_faction.find('relations'),
                            'relation', lambda e: e.get('faction'))
    new_map = _first_by_key(new_faction.find('relations'),
                            'relation', lambda e: e.get('faction'))
    out: list[str] = []
    for key in sorted(new_map.keys() - old_map.keys()):
        val = new_map[key].get('relation')
        out.append(f'relation[faction={key}] added={val}')
    for key in sorted(old_map.keys() - new_map.keys()):
        val = old_map[key].get('relation')
        out.append(f'relation[faction={key}] removed (was {val})')
    for key in sorted(old_map.keys() & new_map.keys()):
        ov = old_map[key].get('relation')
        nv = new_map[key].get('relation')
        if ov != nv:
            out.append(f'relation[faction={key}] {ov}→{nv}')
    return out


def _first_by_key(container: Optional[ET.Element], child_tag: str,
                  key_fn) -> dict:
    """Index direct children of `container` by `key_fn(child)`.

    Last-wins on collisions — the uniqueness-check emits the incomplete
    that flags the ambiguous pairing.
    """
    out: dict = {}
    if container is None:
        return out
    for el in container.findall(child_tag):
        k = key_fn(el)
        if k is None:
            continue
        out[k] = el
    return out


# ---------- action sub-source ----------


def _emit_action(report, loc_old: Locale, loc_new: Locale,
                 rule_report: _RuleReport) -> list[RuleOutput]:
    outputs: list[RuleOutput] = []
    for rec in report.added:
        outputs.extend(_emit_action_added(rec, loc_new, rule_report))
    for rec in report.removed:
        outputs.extend(_emit_action_removed(rec, loc_old, rule_report))
    for rec in report.modified:
        outputs.extend(_emit_action_modified(rec, loc_old, loc_new,
                                             rule_report))
    return outputs


def _emit_action_added(rec, loc_new: Locale,
                       rule_report: _RuleReport) -> list[RuleOutput]:
    action = rec.element
    aid = rec.key
    name = resolve_attr_ref(action, loc_new, attr='name', fallback=aid)
    classifications = _action_classifications(action)
    srcs = render_sources(None, rec.sources)
    text = _format(name, classifications, srcs, ['NEW'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('action', aid),
        'kind': 'added',
        'subsource': 'action',
        'classifications': classifications,
        'action_id': aid,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_action_removed(rec, loc_old: Locale,
                         rule_report: _RuleReport) -> list[RuleOutput]:
    action = rec.element
    aid = rec.key
    name = resolve_attr_ref(action, loc_old, attr='name', fallback=aid)
    classifications = _action_classifications(action)
    srcs = render_sources(rec.sources, None)
    text = _format(name, classifications, srcs, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('action', aid),
        'kind': 'removed',
        'subsource': 'action',
        'classifications': classifications,
        'action_id': aid,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_action_modified(rec, loc_old: Locale, loc_new: Locale,
                          rule_report: _RuleReport) -> list[RuleOutput]:
    aid = rec.key
    name = resolve_attr_ref(rec.new, loc_new, attr='name', fallback=aid)
    if name == aid:
        name = resolve_attr_ref(rec.old, loc_old, attr='name', fallback=aid)
    classifications = _action_classifications(rec.new)

    changes: list[str] = []
    # Top-level attrs.
    for a in ACTION_ATTRS:
        ov = rec.old.get(a)
        nv = rec.new.get(a)
        if ov != nv:
            changes.append(f'{a} {ov}→{nv}')
    # Singleton children.
    for tag in ACTION_SINGLETONS:
        changes.extend(_diff_singleton(rec.old, rec.new, tag))
    # <cost> / <reward>.
    changes.extend(_diff_cost_or_reward(rec.old, rec.new, 'cost'))
    changes.extend(_diff_cost_or_reward(rec.old, rec.new, 'reward'))
    # <params>.
    changes.extend(_diff_params(rec.old, rec.new))

    if not changes:
        return []

    srcs = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, srcs, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': ('action', aid),
        'kind': 'modified',
        'subsource': 'action',
        'classifications': classifications,
        'action_id': aid,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'sources': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]


def _action_classifications(action: ET.Element) -> list[str]:
    """Return [category] minus the generic filter.

    Spec says `["action", @category]` — the `action` token is generic and
    gets stripped.
    """
    out: list[str] = ['action']
    cat = action.get('category')
    if cat:
        out.append(cat)
    return [t for t in out if t not in _GENERIC_FILTER]


def _check_action_children(action: Optional[ET.Element], aid: str,
                           rule_report: _RuleReport,
                           dedup: Optional[set] = None) -> None:
    """Any direct child tag not in ACTION_KNOWN_CHILDREN → incomplete with
    reason `'no_child_matcher'`. Emits one failure per distinct unknown tag
    per action, de-duping across the old/new scan via the caller-supplied
    set.
    """
    if action is None:
        return
    seen_local: set[str] = set()
    for child in action:
        tag = child.tag
        if tag in ACTION_KNOWN_CHILDREN:
            continue
        if tag in seen_local:
            continue
        seen_local.add(tag)
        key = (aid, tag)
        if dedup is not None:
            if key in dedup:
                continue
            dedup.add(key)
        rule_report.failures.append((
            f'action {aid} has unhandled child <{tag}>',
            {
                'reason': 'no_child_matcher',
                'action_id': aid,
                'subtree': tag,
                'affected_keys': [('action', aid)],
            },
        ))


def _check_params_uniqueness(action: Optional[ET.Element], aid: str,
                             rule_report: _RuleReport,
                             dedup: Optional[set] = None) -> None:
    """Duplicate @name within a single <params> block, or duplicate @name
    within a single <param>'s <input_param> children → incomplete with
    reason `'param_name_not_unique'`. Emits one failure per duplicate name
    per action, de-duping across calls via the caller-supplied set.
    """
    if action is None:
        return
    for params in action.findall('params'):
        param_counts: dict[str, int] = {}
        for p in params.findall('param'):
            n = p.get('name')
            if n is None:
                continue
            param_counts[n] = param_counts.get(n, 0) + 1
        for n in sorted([k for k, c in param_counts.items() if c > 1]):
            key = (aid, 'params', n)
            if dedup is not None:
                if key in dedup:
                    continue
                dedup.add(key)
            rule_report.failures.append((
                f'action {aid} <params> has duplicate @name={n}',
                {
                    'reason': 'param_name_not_unique',
                    'action_id': aid,
                    'param_name': n,
                    'level': 'params',
                    'affected_keys': [('action', aid)],
                },
            ))
        for p in params.findall('param'):
            ip_counts: dict[str, int] = {}
            for ip in p.findall('input_param'):
                n = ip.get('name')
                if n is None:
                    continue
                ip_counts[n] = ip_counts.get(n, 0) + 1
            for n in sorted([k for k, c in ip_counts.items() if c > 1]):
                pn = p.get('name') or '<anon>'
                key = (aid, f'input_param:{pn}', n)
                if dedup is not None:
                    if key in dedup:
                        continue
                    dedup.add(key)
                rule_report.failures.append((
                    f'action {aid} param[{pn}] has duplicate '
                    f'<input_param @name={n}>',
                    {
                        'reason': 'param_name_not_unique',
                        'action_id': aid,
                        'param_name': pn,
                        'input_param_name': n,
                        'level': 'input_param',
                        'affected_keys': [('action', aid)],
                    },
                ))


def _diff_singleton(old_action: ET.Element, new_action: ET.Element,
                    tag: str) -> list[str]:
    """Diff a singleton child element's attributes directly.

    If one side has the element and the other doesn't, emit added/removed.
    Otherwise, emit one line per changed attribute.
    """
    old_el = old_action.find(tag)
    new_el = new_action.find(tag)
    if old_el is None and new_el is None:
        return []
    if old_el is None:
        attrs = ', '.join(f'{k}={v}' for k, v in sorted(new_el.attrib.items()))
        return [f'{tag} added ({attrs})' if attrs else f'{tag} added']
    if new_el is None:
        return [f'{tag} removed']
    out: list[str] = []
    for a in sorted(set(old_el.attrib) | set(new_el.attrib)):
        ov = old_el.get(a)
        nv = new_el.get(a)
        if ov != nv:
            out.append(f'{tag} {a} {ov}→{nv}')
    return out


def _canonical_attr_signature(el: ET.Element) -> tuple:
    """Canonical signature tuple for multiset matching when no keyed attr."""
    return tuple(sorted(el.attrib.items()))


def _diff_cost_or_reward(old_action: ET.Element, new_action: ET.Element,
                         tag: str) -> list[str]:
    """Diff <cost> / <reward> root + nested <ware>-keyed subtree entries.

    - Top-level attrs on the singleton <cost>/<reward> element diff directly.
    - Nested `<ware ware="...">` entries (under any depth): keyed by `@ware`.
    - Nested entries WITHOUT `@ware`: multiset by canonical attr signature.
    """
    old_el = old_action.find(tag)
    new_el = new_action.find(tag)
    if old_el is None and new_el is None:
        return []
    out: list[str] = []
    if old_el is None:
        out.append(f'{tag} added')
        return out
    if new_el is None:
        out.append(f'{tag} removed')
        return out
    # Root attrs.
    for a in sorted(set(old_el.attrib) | set(new_el.attrib)):
        ov = old_el.get(a)
        nv = new_el.get(a)
        if ov != nv:
            out.append(f'{tag} {a} {ov}→{nv}')
    # Collect descendant <ware> entries; plus other descendant elements.
    old_wares_keyed, old_wares_multi = _collect_wares(old_el)
    new_wares_keyed, new_wares_multi = _collect_wares(new_el)
    for w in sorted(new_wares_keyed.keys() - old_wares_keyed.keys()):
        attrs = _fmt_attrs(new_wares_keyed[w].attrib)
        out.append(f'{tag}.ware[{w}] added ({attrs})' if attrs
                   else f'{tag}.ware[{w}] added')
    for w in sorted(old_wares_keyed.keys() - new_wares_keyed.keys()):
        attrs = _fmt_attrs(old_wares_keyed[w].attrib)
        out.append(f'{tag}.ware[{w}] removed (was {attrs})' if attrs
                   else f'{tag}.ware[{w}] removed')
    for w in sorted(old_wares_keyed.keys() & new_wares_keyed.keys()):
        o_el = old_wares_keyed[w]
        n_el = new_wares_keyed[w]
        for a in sorted(set(o_el.attrib) | set(n_el.attrib)):
            if a == 'ware':
                continue
            ov = o_el.get(a)
            nv = n_el.get(a)
            if ov != nv:
                out.append(f'{tag}.ware[{w}] {a} {ov}→{nv}')
    # Non-ware multiset diff: signature tuples.
    old_sigs = sorted(old_wares_multi)
    new_sigs = sorted(new_wares_multi)
    if old_sigs != new_sigs:
        # Report as set-style add/remove of signatures.
        o_set = list(old_sigs)
        n_set = list(new_sigs)
        # Multiset diff preserving counts.
        for sig in n_set:
            if sig in o_set:
                o_set.remove(sig)
            else:
                out.append(f'{tag}.<non-ware> added {_fmt_sig(sig)}')
        for sig in o_set:
            out.append(f'{tag}.<non-ware> removed {_fmt_sig(sig)}')
    return out


def _collect_wares(root: ET.Element) -> tuple[dict[str, ET.Element],
                                                list[tuple]]:
    """Walk descendants collecting <ware> entries. Returns
    (keyed_by_@ware, multiset_of_signatures_for_unkeyed_non-root).

    Non-<ware> descendants (e.g., `<wares tags="bribe">`) themselves are
    not reported — their attributes are part of the containing subtree
    signature by virtue of being the <ware> element's siblings. Unkeyed
    entries fall through the multiset path.
    """
    keyed: dict[str, ET.Element] = {}
    multi: list[tuple] = []
    for el in root.iter():
        if el is root:
            continue
        if el.tag == 'ware':
            w = el.get('ware')
            if w:
                keyed[w] = el
            else:
                multi.append(_canonical_attr_signature(el))
    return keyed, multi


def _fmt_attrs(attrib: dict) -> str:
    return ', '.join(f'{k}={v}' for k, v in sorted(attrib.items()))


def _fmt_sig(sig: tuple) -> str:
    return '(' + ', '.join(f'{k}={v}' for k, v in sig) + ')'


def _diff_params(old_action: ET.Element, new_action: ET.Element) -> list[str]:
    """Diff <params>/<param> (keyed by @name) incl. nested <input_param>."""
    old_params = old_action.find('params')
    new_params = new_action.find('params')
    if old_params is None and new_params is None:
        return []
    if old_params is None:
        return ['params added']
    if new_params is None:
        return ['params removed']
    out: list[str] = []
    old_map = _first_by_key(old_params, 'param', lambda e: e.get('name'))
    new_map = _first_by_key(new_params, 'param', lambda e: e.get('name'))
    for name in sorted(new_map.keys() - old_map.keys()):
        out.append(f'params.param[{name}] added')
    for name in sorted(old_map.keys() - new_map.keys()):
        out.append(f'params.param[{name}] removed')
    for name in sorted(old_map.keys() & new_map.keys()):
        out.extend(_diff_param(old_map[name], new_map[name], name))
    return out


def _diff_param(old_p: ET.Element, new_p: ET.Element, name: str) -> list[str]:
    """Diff one <param> element: its attrs + nested <input_param> children."""
    out: list[str] = []
    for a in sorted(set(old_p.attrib) | set(new_p.attrib)):
        if a == 'name':
            continue
        ov = old_p.get(a)
        nv = new_p.get(a)
        if ov != nv:
            out.append(f'params.param[{name}] {a} {ov}→{nv}')
    old_ips = _first_by_key(old_p, 'input_param', lambda e: e.get('name'))
    new_ips = _first_by_key(new_p, 'input_param', lambda e: e.get('name'))
    for ip_name in sorted(new_ips.keys() - old_ips.keys()):
        out.append(f'params.param[{name}].input_param[{ip_name}] added')
    for ip_name in sorted(old_ips.keys() - new_ips.keys()):
        out.append(f'params.param[{name}].input_param[{ip_name}] removed')
    for ip_name in sorted(old_ips.keys() & new_ips.keys()):
        old_ip = old_ips[ip_name]
        new_ip = new_ips[ip_name]
        for a in sorted(set(old_ip.attrib) | set(new_ip.attrib)):
            if a == 'name':
                continue
            ov = old_ip.get(a)
            nv = new_ip.get(a)
            if ov != nv:
                out.append(
                    f'params.param[{name}].input_param[{ip_name}] '
                    f'{a} {ov}→{nv}'
                )
    return out


# ---------- formatting ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'
