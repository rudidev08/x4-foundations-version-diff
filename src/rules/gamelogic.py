"""Gamelogic rule: emit outputs for aiscript, behaviour, and scriptproperty
changes.

Three sub-sources share the `gamelogic` tag, distinguished by `extras.subsource`:

- **`aiscript`** — HYBRID file-level + patch-engine materialization. Each
  filename under `aiscripts/` (core) and `extensions/*/aiscripts/` is
  materialized per-version by applying every DLC `<diff>` patch in alphabetical
  DLC order onto the core script. The effective script is serialized via
  `src.lib.canonical_xml.canonical_bytes` and the two effective byte streams
  are diffed via `src.lib.file_level.render_modified`. Key: `(subsource,
  filename_stem)`. Classifications: `[subsource, filename_prefix]` where
  `filename_prefix` is the substring up to the first `.` or `_`, mapped through
  a fixed token set (`fight`/`interrupt`/`build`/`move`/`order`/`trade`/`plan`/
  `anon`). Patch failures are routed through a local `_AiscriptReport` that
  rides `forward_incomplete_many` alongside the entity-diff subsources.
- **`behaviour`** — entity-diff on `libraries/behaviours.xml`. Key:
  `(subsource, (set_name, parent_collection_tag, behaviour_name))` since a
  behaviour nests inside `<set name="...">/<normal|evade|...>` — neither
  ancestor alone disambiguates. Fields: full attribute set of `<behaviour>`.
  Any unexpected child tag under `<behaviour>` flags the row incomplete with
  reason `unhandled_child_tag` (child tag whitelist is
  `{param, precondition, script}`; real 9.00B6 data has no children).
- **`scriptproperty`** — entity-diff on `libraries/scriptproperties.xml`. Key:
  `(subsource, (datatype_name, property_name))`. Fields: full attribute set
  of `<property>`. Same child-whitelist rule applies (`param` keyed by `@name`,
  `example` as a multiset signature); real data has no children.

Composite-tuple keys don't match the bare-name `affected_keys` that
`_infer_affected_keys` produces from XPath selectors. Before
`forward_incomplete_many`, the rule rewrites bare-name `affected_keys` on the
behaviour + scriptproperty reports to `[]` so the subsource-wide contamination
path triggers.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.lib.canonical_xml import canonical_bytes
from src.lib.check_incomplete import forward_incomplete_many, forward_warnings
from src.lib.entity_diff import (
    apply_patch, diff_library, _OpError, XPathError,
)
from src.lib.file_level import render_modified
from src.lib.paths import source_of
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'gamelogic'

# Filename-prefix → classification token. Enumerated (NOT open-ended): any
# filename whose prefix isn't in this map yields an empty classification list
# beyond `subsource`.
_AISCRIPT_PREFIX_MAP = {
    'fight': 'fight',
    'interrupt': 'interrupt',
    'build': 'build',
    'move': 'move',
    'order': 'order',
    'trade': 'trade',
    'plan': 'plan',
    'anon': 'anon',
}

# Whitelisted child tags under <behaviour>. Adding new entries here requires
# extending the diffing logic in `_diff_behaviour_children` too.
_BEHAVIOUR_CHILD_TAGS = frozenset({'param', 'precondition', 'script'})

# Whitelisted child tags under <property>. Same extension contract as above.
_SCRIPTPROPERTY_CHILD_TAGS = frozenset({'param', 'example'})


# ---------- aiscript report (file-level) ----------


@dataclass
class _AiscriptReport:
    """Minimal DiffReport-shaped surface for aiscript file-level processing.

    Rides `forward_incomplete_many` alongside the two entity-diff reports so
    patch failures during aiscript materialization contaminate the right
    output scope.
    """
    warnings: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


# ---------- top-level entry ----------


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit gamelogic rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused — the rule drives itself
    off the aiscripts tree + two library files.
    """
    outputs: list[RuleOutput] = []
    aiscript_report = _AiscriptReport()
    outputs.extend(_emit_aiscripts(old_root, new_root, aiscript_report))

    behaviour_report, bh_outputs = _emit_behaviours(old_root, new_root)
    outputs.extend(bh_outputs)

    scriptproperty_report, sp_outputs = _emit_scriptproperties(old_root, new_root)
    outputs.extend(sp_outputs)

    # Composite-tuple keys in this rule's outputs don't match the bare-name
    # `affected_keys` strings that `_infer_affected_keys` produces from XPath
    # selectors. Force subsource-wide contamination for bare-string
    # affected_keys on the entity-diff reports — aiscript failures already
    # carry tuple keys of the form `('aiscript', filename_stem)` and don't
    # need rewriting.
    for report, _label in [(behaviour_report, 'behaviour'),
                           (scriptproperty_report, 'scriptproperty')]:
        for _text, extras in report.failures:
            if any(isinstance(k, str)
                   for k in extras.get('affected_keys', [])):
                extras['affected_keys'] = []

    forward_incomplete_many(
        [
            (aiscript_report, 'aiscript'),
            (behaviour_report, 'behaviour'),
            (scriptproperty_report, 'scriptproperty'),
        ],
        outputs, tag=TAG,
    )
    for report in (behaviour_report, scriptproperty_report):
        forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


# ---------- aiscript sub-source ----------


_PREFIX_SPLIT = re.compile(r'[._]')


def _aiscript_prefix(filename: str) -> Optional[str]:
    """Return the classification token for an aiscript filename, or None.

    Prefix = characters up to (not including) the first `.` OR `_`, whichever
    comes first. Maps through `_AISCRIPT_PREFIX_MAP`; filenames whose prefix
    isn't in the map return None (empty extra classification).
    """
    if not filename:
        return None
    head = _PREFIX_SPLIT.split(Path(filename).stem, maxsplit=1)[0]
    return _AISCRIPT_PREFIX_MAP.get(head)


def _enumerate_aiscript_filenames(root: Path) -> set[str]:
    """Return the set of aiscript filenames present in a version tree.

    Union of core `aiscripts/*.xml` and `extensions/*/aiscripts/*.xml` stems.
    A filename counts even if it's only present as a DLC patch (core-absent
    cases). Skips .xsd schema files.
    """
    names: set[str] = set()
    for glob in ('aiscripts/*.xml', 'extensions/*/aiscripts/*.xml'):
        for p in root.glob(glob):
            if p.is_file() and p.suffix == '.xml':
                names.add(p.name)
    return names


def _materialize_aiscript(root: Path, filename: str,
                          report: _AiscriptReport
                          ) -> tuple[Optional[ET.Element], set[str]]:
    """Build the effective aiscript tree for one filename in one version.

    Returns `(effective_root, sources_set)`. `effective_root` is None if the
    file is absent in both core AND every DLC (treat as non-existent for
    add/remove lifecycle). `sources_set` reflects every contributor ('core'
    + DLC short names) that supplied bytes for this effective script.
    """
    sources: set[str] = set()
    entity_key = [('aiscript', Path(filename).stem)]
    core_path = root / 'aiscripts' / filename
    if core_path.is_file():
        try:
            eff = ET.parse(core_path).getroot()
            sources.add('core')
        except ET.ParseError as e:
            report.failures.append((
                f'parse error aiscripts/{filename}',
                {'reason': 'parse_error', 'detail': str(e),
                 'affected_keys': list(entity_key)},
            ))
            eff = None
    else:
        eff = None

    # Alphabetical DLC order — same as diff_library.
    for dlc_path in sorted(root.glob(f'extensions/*/aiscripts/{filename}')):
        rel = str(dlc_path.relative_to(root))
        short = source_of(rel)
        try:
            dlc_root = ET.parse(dlc_path).getroot()
        except ET.ParseError as e:
            report.failures.append((
                f'parse error {rel}',
                {'reason': 'parse_error', 'detail': str(e),
                 'affected_keys': list(entity_key)},
            ))
            continue
        if eff is None:
            # Core-absent: the DLC patch becomes the base. A `<diff>`-wrapped
            # DLC file has nothing to patch onto yet — treat that as an error;
            # a native fragment root IS the effective script.
            if dlc_root.tag == 'diff':
                report.failures.append((
                    f'DLC patch {rel} has no core base script',
                    {'reason': 'patch_no_base',
                     'affected_keys': list(entity_key)},
                ))
                continue
            eff = dlc_root
            sources.add(short)
            continue
        try:
            failures, warnings = apply_patch(eff, dlc_root)
        except (XPathError, _OpError) as e:
            report.failures.append((
                f'apply_patch error {rel}',
                {'reason': getattr(e, 'reason', 'unknown'),
                 'detail': str(e),
                 'affected_keys': list(entity_key)},
            ))
            continue
        # Tag failures + warnings with the aiscript entity key so
        # contamination scoping nails this aiscript specifically.
        for text, extras in failures:
            tagged = dict(extras)
            tagged['affected_keys'] = list(entity_key)
            report.failures.append((f'{rel}: {text}', tagged))
        for text, extras in warnings:
            tagged = dict(extras)
            tagged.setdefault('aiscript_file', filename)
            report.warnings.append((f'{rel}: {text}', tagged))
        sources.add(short)
    return eff, sources


def _emit_aiscripts(old_root: Path, new_root: Path,
                    report: _AiscriptReport) -> list[RuleOutput]:
    """Process every aiscript filename present in either version.

    Per filename: materialize both sides, canonicalize, then diff via
    `render_modified`. Emits one RuleOutput per filename with a lifecycle
    (added/removed/modified); unchanged files emit nothing.
    """
    outputs: list[RuleOutput] = []
    old_names = _enumerate_aiscript_filenames(old_root)
    new_names = _enumerate_aiscript_filenames(new_root)
    all_names = sorted(old_names | new_names)

    for filename in all_names:
        stem = Path(filename).stem
        old_eff, old_sources = (
            _materialize_aiscript(old_root, filename, report)
            if filename in old_names else (None, set())
        )
        new_eff, new_sources = (
            _materialize_aiscript(new_root, filename, report)
            if filename in new_names else (None, set())
        )

        old_bytes = canonical_bytes(old_eff) if old_eff is not None else None
        new_bytes = canonical_bytes(new_eff) if new_eff is not None else None

        if old_bytes is None and new_bytes is None:
            continue
        if old_bytes == new_bytes:
            continue

        display = _aiscript_display_name(new_eff, old_eff, stem)
        prefix_token = _aiscript_prefix(filename)
        classifications = ['aiscript']
        if prefix_token:
            classifications.append(prefix_token)

        # render_modified builds the diff body + extras (diff, added_lines,
        # ...); we keep its extras and rewrite the text to match the
        # `[tag] name (cls) [srcs]: body` shape every other rule uses.
        rel = f'aiscripts/{filename}'
        _, extras = render_modified(
            rel, old_bytes, new_bytes, tag=TAG, name=display,
        )

        if old_bytes is None:
            kind = 'added'
            summary = f'ADDED (+{extras["added_lines"]} lines)'
        elif new_bytes is None:
            kind = 'removed'
            summary = f'REMOVED (-{extras["removed_lines"]} lines)'
        else:
            kind = 'modified'
            summary = (f'modified (+{extras["added_lines"]}'
                       f'/-{extras["removed_lines"]} lines)')

        old_src = sorted(old_sources) if old_sources else None
        new_src = sorted(new_sources) if new_sources else None
        sources_label = render_sources(old_src, new_src)
        text = _format_row(display, classifications, sources_label, [summary])

        extras['entity_key'] = ('aiscript', stem)
        extras['kind'] = kind
        extras['subsource'] = 'aiscript'
        extras['classifications'] = classifications
        extras['filename'] = filename
        if old_src is not None:
            extras['old_sources'] = old_src
        if new_src is not None:
            extras['new_sources'] = new_src
        outputs.append(RuleOutput(tag=TAG, text=text, extras=extras))
    return outputs


def _aiscript_display_name(new_eff: Optional[ET.Element],
                           old_eff: Optional[ET.Element],
                           stem: str) -> str:
    """Resolve display name from the effective aiscript root `@name`."""
    for eff in (new_eff, old_eff):
        if eff is None:
            continue
        # Effective root is typically an <aiscript> element directly.
        if eff.tag == 'aiscript':
            name = eff.get('name')
            if name:
                return name
        # Some scripts wrap under <aiscripts><aiscript>...; handle both.
        inner = eff.find('aiscript')
        if inner is not None and inner.get('name'):
            return inner.get('name')
    return stem


# ---------- behaviour sub-source ----------


def _emit_behaviours(old_root: Path, new_root: Path
                     ) -> tuple[object, list[RuleOutput]]:
    """Materialize `libraries/behaviours.xml` and emit per-behaviour rows.

    Uses identity-key diff_library to reach the effective trees, then
    re-indexes both sides by composite `(set_name, parent_collection_tag,
    behaviour_name)`.
    """
    base_report = diff_library(
        old_root, new_root, 'libraries/behaviours.xml', './/behaviour',
        key_fn=lambda e: id(e),
        key_fn_identity='gamelogic_behaviour_identity',
    )
    report = _EntityDiffReport(base_report)

    old_tree = base_report.effective_old_root
    new_tree = base_report.effective_new_root
    old_map = _index_behaviours(old_tree)
    new_map = _index_behaviours(new_tree)

    outputs: list[RuleOutput] = []
    all_keys = sorted(set(old_map) | set(new_map),
                      key=_tuple_key_sort)
    for composite in all_keys:
        old_el = old_map.get(composite)
        new_el = new_map.get(composite)
        if old_el is not None and new_el is not None:
            row = _emit_behaviour_modified(composite, old_el, new_el, report)
            if row is not None:
                outputs.append(row)
        elif old_el is None and new_el is not None:
            outputs.append(_emit_behaviour_added(composite, new_el, report))
        else:  # new_el is None
            outputs.append(_emit_behaviour_removed(composite, old_el, report))
    return report, outputs


def _index_behaviours(tree_root: Optional[ET.Element]
                      ) -> dict[tuple, ET.Element]:
    """Return `{(set_name, parent_collection_tag, behaviour_name):
    <behaviour>}`.

    Walks the tree once, records each `<behaviour>` under the enclosing
    `<set @name>` and its immediate parent collection tag. Behaviours whose
    ancestry lacks a `<set>` are skipped (defensive — real data always has
    one).
    """
    out: dict[tuple, ET.Element] = {}
    if tree_root is None:
        return out
    # Build a parent-map once so ancestor walks are O(1).
    pmap: dict[int, ET.Element] = {id(c): p for p in tree_root.iter()
                                   for c in p}
    for b in tree_root.iter('behaviour'):
        behaviour_name = b.get('name')
        if not behaviour_name:
            continue
        parent = pmap.get(id(b))
        if parent is None:
            continue
        parent_tag = parent.tag
        set_name = None
        cur = parent
        seen: set[int] = set()
        while id(cur) in pmap and id(cur) not in seen:
            seen.add(id(cur))
            p = pmap[id(cur)]
            if p.tag == 'set' and p.get('name') is not None:
                set_name = p.get('name')
                break
            cur = p
        if set_name is None:
            # Defensive — real data always nests behaviours under <set>.
            continue
        key = (set_name, parent_tag, behaviour_name)
        out[key] = b
    return out


def _emit_behaviour_added(composite: tuple, el: ET.Element,
                          report) -> RuleOutput:
    set_name, parent_tag, behaviour_name = composite
    has_bad = _check_behaviour_children(el, composite, report)
    classifications = ['behaviour', set_name, parent_tag]
    attrs = _collect_all_attrs(el, exclude=('name',))
    parts = ['NEW']
    parts.extend(f'{k}={v}' for k, v in sorted(attrs.items()))
    srcs = render_sources(None, ['core'])
    text = _format_row(behaviour_name, classifications, srcs, parts)
    extras = {
        'entity_key': ('behaviour', composite),
        'kind': 'added',
        'subsource': 'behaviour',
        'classifications': classifications,
        'set_name': set_name,
        'parent_collection_tag': parent_tag,
        'behaviour_name': behaviour_name,
    }
    if has_bad:
        extras['incomplete'] = True
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _emit_behaviour_removed(composite: tuple, el: ET.Element,
                            report) -> RuleOutput:
    set_name, parent_tag, behaviour_name = composite
    _check_behaviour_children(el, composite, report)
    classifications = ['behaviour', set_name, parent_tag]
    srcs = render_sources(['core'], None)
    text = _format_row(behaviour_name, classifications, srcs, ['REMOVED'])
    extras = {
        'entity_key': ('behaviour', composite),
        'kind': 'removed',
        'subsource': 'behaviour',
        'classifications': classifications,
        'set_name': set_name,
        'parent_collection_tag': parent_tag,
        'behaviour_name': behaviour_name,
    }
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _emit_behaviour_modified(composite: tuple, old_el: ET.Element,
                             new_el: ET.Element,
                             report) -> Optional[RuleOutput]:
    set_name, parent_tag, behaviour_name = composite
    new_bad = _check_behaviour_children(new_el, composite, report)
    old_bad = _check_behaviour_children(old_el, composite, report)

    changes: list[str] = []
    # Attribute diff excluding `name`.
    old_attrs = _collect_all_attrs(old_el, exclude=('name',))
    new_attrs = _collect_all_attrs(new_el, exclude=('name',))
    changes.extend(_diff_attr_map(old_attrs, new_attrs))
    # Child diffs (whitelisted tags only).
    changes.extend(_diff_behaviour_children(old_el, new_el))

    if not changes:
        return None

    classifications = ['behaviour', set_name, parent_tag]
    srcs = render_sources(['core'], ['core'])
    text = _format_row(behaviour_name, classifications, srcs, changes)
    extras = {
        'entity_key': ('behaviour', composite),
        'kind': 'modified',
        'subsource': 'behaviour',
        'classifications': classifications,
        'set_name': set_name,
        'parent_collection_tag': parent_tag,
        'behaviour_name': behaviour_name,
    }
    if new_bad or old_bad:
        extras['incomplete'] = True
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _check_behaviour_children(el: ET.Element, composite: tuple,
                              report) -> bool:
    """Assert every direct child tag is in `_BEHAVIOUR_CHILD_TAGS`.

    Appends one failure per unknown tag to `report.failures` with reason
    `unhandled_child_tag` and composite-tuple `affected_keys` so
    contamination nails the specific behaviour. Returns True iff any
    unknown tag was seen.
    """
    bad = {c.tag for c in el if c.tag not in _BEHAVIOUR_CHILD_TAGS}
    for tag in sorted(bad):
        report.failures.append((
            f'behaviour {composite[2]} in set={composite[0]}/'
            f'{composite[1]} has unhandled child <{tag}>',
            {'reason': 'unhandled_child_tag',
             'child_tag': tag,
             'set_name': composite[0],
             'parent_collection_tag': composite[1],
             'behaviour_name': composite[2],
             'affected_keys': [('behaviour', composite)]},
        ))
    return bool(bad)


def _diff_behaviour_children(old_el: ET.Element,
                             new_el: ET.Element) -> list[str]:
    """Diff whitelisted child collections under <behaviour>.

    - `<param>` children keyed by `@name`.
    - `<precondition>` / `<script>` singletons — attrs diffed directly.

    Elements outside the whitelist are handled by `_check_behaviour_children`
    and produce rule-level incompletes; we silently skip them here.
    """
    out: list[str] = []
    # Params keyed by @name.
    old_params = _index_named_children(old_el, 'param')
    new_params = _index_named_children(new_el, 'param')
    for name in sorted(new_params.keys() - old_params.keys()):
        out.append(f'param[{name}] added')
    for name in sorted(old_params.keys() - new_params.keys()):
        out.append(f'param[{name}] removed')
    for name in sorted(old_params.keys() & new_params.keys()):
        o = old_params[name]
        n = new_params[name]
        for a in sorted(set(o.attrib) | set(n.attrib)):
            if a == 'name':
                continue
            if o.get(a) != n.get(a):
                out.append(f'param[{name}] {a} {o.get(a)}→{n.get(a)}')
    # Singletons.
    for tag in ('precondition', 'script'):
        o = old_el.find(tag)
        n = new_el.find(tag)
        if o is None and n is None:
            continue
        if o is None:
            attrs = ', '.join(f'{k}={v}' for k, v in sorted(n.attrib.items()))
            out.append(f'{tag} added' + (f' ({attrs})' if attrs else ''))
            continue
        if n is None:
            out.append(f'{tag} removed')
            continue
        for a in sorted(set(o.attrib) | set(n.attrib)):
            if o.get(a) != n.get(a):
                out.append(f'{tag} {a} {o.get(a)}→{n.get(a)}')
    return out


# ---------- scriptproperty sub-source ----------


def _emit_scriptproperties(old_root: Path, new_root: Path
                            ) -> tuple[object, list[RuleOutput]]:
    """Materialize `libraries/scriptproperties.xml` and emit per-property
    rows. Uses identity keys + manual re-indexing to disambiguate properties
    whose same @name appears under different `<datatype @name>` parents.
    """
    base_report = diff_library(
        old_root, new_root, 'libraries/scriptproperties.xml', './/property',
        key_fn=lambda e: id(e),
        key_fn_identity='gamelogic_scriptproperty_identity',
    )
    report = _EntityDiffReport(base_report)

    old_tree = base_report.effective_old_root
    new_tree = base_report.effective_new_root
    old_map = _index_properties(old_tree)
    new_map = _index_properties(new_tree)

    outputs: list[RuleOutput] = []
    all_keys = sorted(set(old_map) | set(new_map),
                      key=_tuple_key_sort)
    for composite in all_keys:
        old_el = old_map.get(composite)
        new_el = new_map.get(composite)
        if old_el is not None and new_el is not None:
            row = _emit_scriptproperty_modified(composite, old_el, new_el,
                                                report)
            if row is not None:
                outputs.append(row)
        elif old_el is None and new_el is not None:
            outputs.append(_emit_scriptproperty_added(composite, new_el,
                                                     report))
        else:
            outputs.append(_emit_scriptproperty_removed(composite, old_el,
                                                       report))
    return report, outputs


def _index_properties(tree_root: Optional[ET.Element]
                      ) -> dict[tuple, ET.Element]:
    """Return `{(datatype_name, property_name): <property>}`.

    Duplicates within a single tree overwrite (last-wins) — duplicate
    property names within one datatype are a data bug the rule does not
    defend against. Real 9.00B6 data has none.
    """
    out: dict[tuple, ET.Element] = {}
    if tree_root is None:
        return out
    for datatype in tree_root.iter('datatype'):
        dt_name = datatype.get('name')
        if dt_name is None:
            continue
        for prop in datatype.iter('property'):
            pname = prop.get('name')
            if pname is None:
                continue
            out[(dt_name, pname)] = prop
    return out


def _emit_scriptproperty_added(composite: tuple, el: ET.Element,
                               report) -> RuleOutput:
    dt_name, pname = composite
    has_bad = _check_scriptproperty_children(el, composite, report)
    classifications = ['scriptproperty']
    attrs = _collect_all_attrs(el, exclude=('name',))
    parts = ['NEW']
    parts.extend(f'{k}={v}' for k, v in sorted(attrs.items()))
    srcs = render_sources(None, ['core'])
    display = f'{dt_name}.{pname}'
    text = _format_row(display, classifications, srcs, parts)
    extras = {
        'entity_key': ('scriptproperty', composite),
        'kind': 'added',
        'subsource': 'scriptproperty',
        'classifications': classifications,
        'datatype_name': dt_name,
        'property_name': pname,
    }
    if el.get('result') is not None:
        extras['result'] = el.get('result')
    if has_bad:
        extras['incomplete'] = True
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _emit_scriptproperty_removed(composite: tuple, el: ET.Element,
                                 report) -> RuleOutput:
    dt_name, pname = composite
    _check_scriptproperty_children(el, composite, report)
    classifications = ['scriptproperty']
    srcs = render_sources(['core'], None)
    display = f'{dt_name}.{pname}'
    text = _format_row(display, classifications, srcs, ['REMOVED'])
    extras = {
        'entity_key': ('scriptproperty', composite),
        'kind': 'removed',
        'subsource': 'scriptproperty',
        'classifications': classifications,
        'datatype_name': dt_name,
        'property_name': pname,
    }
    if el.get('result') is not None:
        extras['result'] = el.get('result')
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _emit_scriptproperty_modified(composite: tuple, old_el: ET.Element,
                                  new_el: ET.Element,
                                  report) -> Optional[RuleOutput]:
    dt_name, pname = composite
    new_bad = _check_scriptproperty_children(new_el, composite, report)
    old_bad = _check_scriptproperty_children(old_el, composite, report)

    changes: list[str] = []
    old_attrs = _collect_all_attrs(old_el, exclude=('name',))
    new_attrs = _collect_all_attrs(new_el, exclude=('name',))
    changes.extend(_diff_attr_map(old_attrs, new_attrs))
    changes.extend(_diff_scriptproperty_children(old_el, new_el))

    if not changes:
        return None

    classifications = ['scriptproperty']
    srcs = render_sources(['core'], ['core'])
    display = f'{dt_name}.{pname}'
    text = _format_row(display, classifications, srcs, changes)
    extras = {
        'entity_key': ('scriptproperty', composite),
        'kind': 'modified',
        'subsource': 'scriptproperty',
        'classifications': classifications,
        'datatype_name': dt_name,
        'property_name': pname,
    }
    if new_el.get('result') is not None:
        extras['result'] = new_el.get('result')
    if new_bad or old_bad:
        extras['incomplete'] = True
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _check_scriptproperty_children(el: ET.Element, composite: tuple,
                                   report) -> bool:
    """Assert every direct child tag is in `_SCRIPTPROPERTY_CHILD_TAGS`.

    Unknown tags append a failure with reason `unhandled_child_tag` and
    composite-tuple `affected_keys` scoped to the specific property.
    Returns True iff any unknown tag was seen.
    """
    bad = {c.tag for c in el if c.tag not in _SCRIPTPROPERTY_CHILD_TAGS}
    for tag in sorted(bad):
        report.failures.append((
            f'property {composite[0]}.{composite[1]} has unhandled '
            f'child <{tag}>',
            {'reason': 'unhandled_child_tag',
             'child_tag': tag,
             'datatype_name': composite[0],
             'property_name': composite[1],
             'affected_keys': [('scriptproperty', composite)]},
        ))
    return bool(bad)


def _diff_scriptproperty_children(old_el: ET.Element,
                                  new_el: ET.Element) -> list[str]:
    """Diff whitelisted child collections under <property>.

    - `<param>` children keyed by `@name`.
    - `<example>` children as a multiset by canonical attribute signature.
    """
    out: list[str] = []
    # Params by @name.
    old_params = _index_named_children(old_el, 'param')
    new_params = _index_named_children(new_el, 'param')
    for name in sorted(new_params.keys() - old_params.keys()):
        out.append(f'param[{name}] added')
    for name in sorted(old_params.keys() - new_params.keys()):
        out.append(f'param[{name}] removed')
    for name in sorted(old_params.keys() & new_params.keys()):
        o = old_params[name]
        n = new_params[name]
        for a in sorted(set(o.attrib) | set(n.attrib)):
            if a == 'name':
                continue
            if o.get(a) != n.get(a):
                out.append(f'param[{name}] {a} {o.get(a)}→{n.get(a)}')
    # Examples as multiset.
    old_sigs = sorted(_attr_sig(e) for e in old_el.findall('example'))
    new_sigs = sorted(_attr_sig(e) for e in new_el.findall('example'))
    if old_sigs != new_sigs:
        # Multiset diff preserving counts.
        o_set = list(old_sigs)
        for sig in new_sigs:
            if sig in o_set:
                o_set.remove(sig)
            else:
                out.append(f'example added {_fmt_sig(sig)}')
        for sig in o_set:
            out.append(f'example removed {_fmt_sig(sig)}')
    return out


# ---------- shared helpers ----------


def _collect_all_attrs(el: ET.Element, exclude: tuple[str, ...] = ()
                       ) -> dict[str, str]:
    """Return a copy of `el.attrib` minus the excluded keys."""
    return {k: v for k, v in el.attrib.items() if k not in exclude}


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


def _index_named_children(el: ET.Element, tag: str
                          ) -> dict[str, ET.Element]:
    """Index direct `<tag @name>` children by `@name`. Unnamed entries skipped."""
    out: dict[str, ET.Element] = {}
    for c in el.findall(tag):
        n = c.get('name')
        if n is None:
            continue
        out[n] = c
    return out


def _attr_sig(el: ET.Element) -> tuple:
    """Canonical hashable signature for multiset matching."""
    return tuple(sorted(el.attrib.items()))


def _fmt_sig(sig: tuple) -> str:
    return '(' + ', '.join(f'{k}={v}' for k, v in sig) + ')'


def _tuple_key_sort(k: tuple) -> tuple:
    """Sort key that's safe for mixed None/str tuples."""
    return tuple('' if x is None else str(x) for x in k)


def _format_row(name: str, classifications: list[str], sources_label: str,
                parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'


# ---------- DiffReport-shaped wrapper for entity-diff subsources ----------


class _EntityDiffReport:
    """DiffReport-shaped wrapper that owns a single `failures` list.

    Seeds from the underlying `diff_library` report's failures at
    construction and exposes the list for rule-level append (unhandled child
    tags). Rule-level failures carry composite-tuple `affected_keys`; the
    patch-derived failures (with bare-string keys) are rewritten to
    `affected_keys=[]` at the top-level `run()` bridge before
    `forward_incomplete_many` runs.
    """
    def __init__(self, base_report):
        self.failures: list = list(getattr(base_report, 'failures', []) or [])
        self.warnings: list = list(getattr(base_report, 'warnings', []) or [])

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)
