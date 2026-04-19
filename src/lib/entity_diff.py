"""Entity-level diff helper for X4 libraries/maps.

Provides:
- An XPath subset evaluator (`xpath_find`).
- The DLC patch engine that materializes one effective tree per side
  (`apply_patch`, `_materialize`).
- `diff_library`: the entry point rules call to get added/removed/modified
  records keyed by an arbitrary `key_fn`.
- Three-tier conflict classification + contributor-set provenance.
"""
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Callable, Hashable, Optional, Union

from src.lib import cache
from src.lib.paths import source_of


class XPathError(RuntimeError):
    pass


@dataclass
class AttrRef:
    parent: ElementTree.Element
    name: str

    @property
    def value(self) -> Optional[str]:
        return self.parent.get(self.name)


# Grammar:
#   xpath   := step (step)*  |  step (step)* '/@' NAME
#   step    := '//' NAME predicate*  |  '/' NAME predicate*  |  NAME predicate*
#   predicate := '[' pred_body ']'
#   pred_body := '@' NAME '=' STRING   |   'not(' xpath_or_bare ')'
#             | literal_int  |  xpath_or_bare
_NAME = r"[A-Za-z_][A-Za-z0-9_\-]*"
_POS_PRED_RE = re.compile(r'^\s*(\d+)\s*$')
_REJECT_POSITION = re.compile(r'\bposition\s*\(\s*\)')
_REJECT_LAST = re.compile(r'\blast\s*\(\s*\)')
_REJECT_ARITH = re.compile(r'\d\s*[+\-*/]')
_KEY_PRED_RE = re.compile(r"\[@(?:id|name)=(['\"])([^'\"]+)\1\]")


def _validate_pred(pred: str) -> None:
    if _REJECT_POSITION.search(pred) or _REJECT_LAST.search(pred) \
            or _REJECT_ARITH.search(pred):
        raise XPathError(f'unsupported predicate: {pred}')


def xpath_find(root: ElementTree.Element, xpath: str,
               document_root: Optional[ElementTree.Element] = None
               ) -> list[Union[ElementTree.Element, AttrRef]]:
    """Find elements or attributes matching xpath from the document root.

    `document_root` is the authoritative `//`-anchor. Defaults to `root`; must
    be threaded explicitly when evaluating predicates against candidate
    elements so `not(//tag)` doesn't mistakenly search the candidate's subtree.
    """
    doc = document_root if document_root is not None else root
    xpath = xpath.strip()
    m = re.match(r'^(.*?)/@(' + _NAME + r')\s*$', xpath)
    if m:
        base_xp, attr_name = m.group(1), m.group(2)
        base_nodes = _find_elems(root, base_xp, doc)
        return [AttrRef(n, attr_name) for n in base_nodes if n.get(attr_name) is not None]
    return _find_elems(root, xpath, doc)


def _find_elems(root: ElementTree.Element, xp: str,
                document_root: Optional[ElementTree.Element] = None) -> list[ElementTree.Element]:
    doc = document_root if document_root is not None else root
    steps = _parse_steps(xp)
    if not steps:
        return []
    absolute = steps[0][3]
    current: list[ElementTree.Element] = [root]
    first = True
    for step in steps:
        axis, tag, preds, _abs = step
        # Split predicates: literal-integer positionals filter the set per
        # parent AFTER the boolean predicates; everything else is per-element.
        bool_preds = [p for p in preds if not _POS_PRED_RE.match(p)]
        pos_preds = [int(_POS_PRED_RE.match(p).group(1))
                     for p in preds if _POS_PRED_RE.match(p)]
        nxt: list[ElementTree.Element] = []
        for context in current:
            if first and absolute:
                cands = [context] if context.tag == tag else []
            elif axis == '//':
                cands = list(context.iter(tag))
                if cands and cands[0] is context:
                    cands = cands[1:]
            else:
                cands = [c for c in context if c.tag == tag]
            filtered = [c for c in cands
                        if all(_eval_pred(c, p, doc) for p in bool_preds)]
            # Apply positionals last, 1-indexed, per-parent. Chained positionals
            # would be pathological in real data; apply in order.
            for n in pos_preds:
                filtered = [filtered[n - 1]] if 1 <= n <= len(filtered) else []
            nxt.extend(filtered)
        current = nxt
        first = False
    return current


def _parse_steps(xp: str) -> list[tuple[str, str, list[str], bool]]:
    """Returns list of (axis, tag, predicates, is_absolute_first_step).

    Leading `/tag`  → absolute, first step matches root tag.
    Leading `//tag` → descendant from anywhere under root.
    Leading `tag`  → treated as child-of-root (relative).
    """
    absolute_first = False
    if xp.startswith('//'):
        xp = xp[2:]
        leading = '//'
    elif xp.startswith('/'):
        xp = xp[1:]
        leading = '/'
        absolute_first = True
    else:
        leading = '/'  # bare "tag" is child-axis per XPath semantics.
        # (Not descendant. `[not(factions)]` applied to <location> tests
        # absence of DIRECT CHILD factions, not descendant factions.)

    out: list[tuple[str, str, list[str], bool]] = []
    position = 0
    axis = leading
    first = True
    while position < len(xp):
        m = re.match(_NAME, xp[position:])
        if not m:
            raise XPathError(f'unexpected at position {position}: {xp[position:]}')
        tag = m.group(0)
        position += len(tag)
        preds: list[str] = []
        while position < len(xp) and xp[position] == '[':
            depth = 1
            end = position + 1
            while end < len(xp) and depth:
                if xp[end] == '[':
                    depth += 1
                elif xp[end] == ']':
                    depth -= 1
                end += 1
            body = xp[position + 1:end - 1]
            _validate_pred(body)
            preds.append(body)
            position = end
        out.append((axis, tag, preds, absolute_first and first))
        first = False
        if position < len(xp):
            if xp[position:position + 2] == '//':
                axis = '//'
                position += 2
            elif xp[position] == '/':
                axis = '/'
                position += 1
            else:
                raise XPathError(f'unexpected at position {position}: {xp[position:]}')
    return out


_EQ_PRED = re.compile(r"^\s*@(" + _NAME + r")\s*=\s*'([^']*)'\s*$")
_NOT_PRED = re.compile(r"^\s*not\s*\((.+)\)\s*$")


def _eval_pred(element: ElementTree.Element, pred: str,
               document_root: Optional[ElementTree.Element] = None) -> bool:
    pred = pred.strip()
    m = _EQ_PRED.match(pred)
    if m:
        return element.get(m.group(1)) == m.group(2)
    m = _NOT_PRED.match(pred)
    if m:
        return not _eval_bare(element, m.group(1).strip(), document_root)
    return _eval_bare(element, pred, document_root)


def _eval_bare(element: ElementTree.Element, bare: str,
               document_root: Optional[ElementTree.Element] = None) -> bool:
    """Evaluate a bare XPath as truthy when matches exist.

    Handles: `//descendant` (anchored to document_root, NOT element's subtree),
    `/child`, `tag`, `tag/child`, `tag[@attribute='v']`, nested `not(...)`, and
    predicates on descendants. `//` MUST search from document_root — threading
    it through is what prevents `[not(//faction[...])]` on a job from
    incorrectly searching only the job's subtree.
    """
    probe = bare.strip()
    if probe.startswith('//'):
        # // anchors to the document root. Use document_root, not element.
        anchor = document_root if document_root is not None else element
        return bool(_find_elems(anchor, probe, document_root))
    elif probe.startswith('/'):
        anchor = document_root if document_root is not None else element
        return bool(_find_elems(anchor, probe, document_root))
    else:
        # Bare path `tag` or `tag/child` is CHILD-axis per XPath semantics.
        # `[not(factions)]` on <location> = no direct child factions (not
        # descendant). The parser's "no leading slash" branch is exactly
        # child-axis-from-element — pass the probe as-is.
        return bool(_find_elems(element, probe, document_root))


def apply_patch(effective_root: ElementTree.Element,
                patch_root: ElementTree.Element
                ) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
    """Apply a <diff>-wrapped op sequence (or native-fragment root) to the
    effective tree in place. Returns (failures, warnings).

    Warnings cover soft signals like `<remove silent="true">` misses — spec
    line 169. Callers forward both via DiffReport.warnings / .failures.
    """
    failures: list[tuple[str, dict]] = []
    warnings: list[tuple[str, dict]] = []
    invalidate_parent_map(effective_root)  # start fresh
    try:
        if patch_root.tag == 'diff':
            operations = list(patch_root)
        else:
            # Native-fragment: wrap each top-level child as <add sel="/<file-root>">child</add>.
            # <file-root> is the DLC file's root tag — same as the core file's root
            # (e.g., <plans>, <loadouts>) — NOT the child tag.
            fragment_root_tag = patch_root.tag
            operations = [_synthesize_add(c, fragment_root_tag) for c in patch_root]
        for operation in operations:
            try:
                _apply_op(effective_root, operation, warnings)
            except _Skip:
                pass
            except (XPathError, _OpError) as e:
                failures.append((f'patch op failed: {operation.tag} sel={operation.get("sel")}', {
                    'reason': e.reason if isinstance(e, _OpError) else 'xpath',
                    'op_tag': operation.tag,
                    'sel': operation.get('sel'),
                    'if': operation.get('if'),
                    'detail': str(e),
                    'affected_keys': _infer_affected_keys(operation.get('sel')),
                }))
        return failures, warnings
    finally:
        invalidate_parent_map(effective_root)


def _infer_affected_keys(selector: Optional[str]) -> list:
    """Best-effort extract entity keys from a selector.

    `//ware[@id='X']` → `['X']`. Both single and double quotes match —
    X4 timelines DLC escapes predicates with `&quot;`, which ElementTree
    decodes to double quotes. Composite-keyed rules inject their own key
    extraction via the entity index; this fallback is for the `@id='X'`
    majority case. Selectors that don't pin an entity yield an empty list,
    which makes forward_incomplete mark ALL outputs from that sub-report.
    """
    if not selector:
        return []
    m = _KEY_PRED_RE.search(selector)
    if m:
        return [m.group(2)]
    return []


class _Skip(Exception):
    pass


class _OpError(Exception):
    def __init__(self, reason, detail=''):
        super().__init__(detail)
        self.reason = reason


def _synthesize_add(child: ElementTree.Element, file_root_tag: str) -> ElementTree.Element:
    """Wrap a native-fragment child as <add sel="/<file-root>">child</add>.

    file_root_tag comes from the DLC file's root element (e.g. 'plans',
    'loadouts', 'ships') — same as the core file's root. This matches the
    effective tree's shape, NOT the child's tag.
    """
    operation = ElementTree.Element('add', attrib={'sel': '/' + file_root_tag})
    operation.append(child)
    return operation


def _apply_op(root: ElementTree.Element, operation: ElementTree.Element,
              warnings: list[tuple[str, dict]]) -> dict[str, list[str]]:
    """Apply op to root. Returns {entity_id: [ref_paths_written]} for entities
    this op actually mutated. Empty dict if op was skipped, failed, or touched
    no id-bearing entities.

    Callers use the return value for provenance attribution so we only record
    contributors for ops that ran successfully — NOT preflight-scanned ops.
    """
    if operation.get('if'):
        if not _eval_if(root, operation.get('if')):
            raise _Skip()
    selector = operation.get('sel')
    if operation.tag == 'add':
        position = operation.get('pos')
        return _do_add(root, selector, list(operation), position)
    elif operation.tag == 'replace':
        return _do_replace(root, selector, operation)
    elif operation.tag == 'remove':
        silent = operation.get('silent') in ('true', '1')
        return _do_remove(root, selector, silent, warnings)
    else:
        raise _OpError('unknown_op', f'op={operation.tag}')


def _eval_if(root, xp):
    """Evaluate `if=` gate. Supports `not(XPATH)` top-level wrapping AND bare XPath.

    `if="not(//faction[@id='terran'])"` is the canonical form;
    `xpath_find` alone only parses paths, not boolean expressions, so this
    wrapper handles the `not(...)` shell before falling through.
    """
    xp = xp.strip()
    m = re.match(r'^\s*not\s*\((.+)\)\s*$', xp)
    if m:
        try:
            return not bool(xpath_find(root, m.group(1).strip()))
        except XPathError:
            raise _OpError('unparseable_if', xp)
    try:
        return bool(xpath_find(root, xp))
    except XPathError:
        raise _OpError('unparseable_if', xp)


def _do_add(root, selector, children, position) -> dict[str, list[str]]:
    try:
        targets = xpath_find(root, selector)
    except XPathError:
        raise _OpError('unsupported_xpath', selector)
    if not targets:
        raise _OpError('add_target_missing', selector)
    touched: dict[str, list[str]] = {}
    invalidate_parent_map(root)  # parent relationships change after insert
    for t in targets:
        if isinstance(t, AttrRef):
            raise _OpError('add_to_attr', selector)
        if position == 'prepend':
            for i, c in enumerate(children):
                t.insert(i, _clone(c))
        elif position == 'after':
            parent = _parent_of(root, t)
            if parent is None:
                raise _OpError('no_parent_for_after', selector)
            index = list(parent).index(t) + 1
            for c in children:
                parent.insert(index, _clone(c))
                index += 1
        elif position == 'before':
            parent = _parent_of(root, t)
            if parent is None:
                raise _OpError('no_parent_for_before', selector)
            index = list(parent).index(t)
            for c in children:
                parent.insert(index, _clone(c))
                index += 1
        else:
            for c in children:
                t.append(_clone(c))
        # Record the target entity (or nearest id-bearing ancestor) — ops that
        # add children INTO an existing entity are writers of that entity, even
        # when the appended children carry no id/name (e.g., `<illegal>` or
        # `<owner>` elements with no id attribute).
        target_entity = _nearest_ancestor_id(root, t)
        if target_entity:
            touched.setdefault(target_entity, [])
        # Attribute inserted children with any id/name they carry, and record any
        # ref-bearing child under them. Currently `<component ref="...">` only; other
        # ref-carrying forms will be generalized in a later refactor when the set
        # expands.
        for c in children:
            entity_id = c.get('id') or c.get('name')
            if entity_id:
                touched.setdefault(entity_id, [])
                for ref_child, attribute in [('component', 'ref')]:
                    inner = c.find(ref_child)
                    if inner is not None and inner.get(attribute) is not None:
                        touched[entity_id].append(f'{ref_child}/@{attribute}')
    return touched


def _do_replace(root, selector, operation) -> dict[str, list[str]]:
    try:
        targets = xpath_find(root, selector)
    except XPathError:
        raise _OpError('unsupported_xpath', selector)
    if not targets:
        raise _OpError('replace_target_missing', selector)
    touched: dict[str, list[str]] = {}
    invalidate_parent_map(root)
    for t in targets:
        if isinstance(t, AttrRef):
            t.parent.set(t.name, (operation.text or '').strip())
            # Walk up to the nearest entity-bearing ancestor.
            entity_id, ref_path = _ancestor_entity_and_ref_path(root, t.parent, selector)
            if entity_id:
                touched.setdefault(entity_id, [])
                if ref_path:
                    touched[entity_id].append(ref_path)
        else:
            new_el = list(operation)[0] if len(operation) else None
            if new_el is None:
                raise _OpError('replace_body_missing', selector)
            parent = _parent_of(root, t)
            if parent is None:
                raise _OpError('no_parent_for_replace', selector)
            index = list(parent).index(t)
            parent.remove(t)
            parent.insert(index, _clone(new_el))
            # Attribute to nearest id-bearing ancestor or the replaced element's id.
            entity_id = new_el.get('id') or new_el.get('name') or \
                        _nearest_ancestor_id(root, parent)
            if entity_id:
                touched.setdefault(entity_id, [])
    return touched


def _ancestor_entity_and_ref_path(root, parent_elem, selector):
    """Walk up from parent_elem to find an id/name bearing ancestor; return
    (entity_id, ref_path) where ref_path is the element/@attribute tail of sel.
    """
    m = re.search(r'/(\w+)/@(\w+)\s*$', selector or '')
    ref_path = f'{m.group(1)}/@{m.group(2)}' if m else None
    # Search ancestors for id/name.
    for anc in _ancestors(root, parent_elem):
        if anc.get('id') or anc.get('name'):
            return (anc.get('id') or anc.get('name'), ref_path)
    return (None, ref_path)


# Parent-map cache: keyed by id(root) so one apply_patch or _materialize call
# builds the map once and reuses it across every _ancestors / _parent_of call.
# Cleared automatically when the tree is mutated (cleared via invalidate_parent_map).
_PARENT_MAP_CACHE: dict[int, dict[int, ElementTree.Element]] = {}


def parent_map(root: ElementTree.Element) -> dict[int, ElementTree.Element]:
    """Return `{id(child): parent}` for every element under `root`. Cached
    on `id(root)`; invalidated automatically when the patch engine mutates
    the tree via `invalidate_parent_map`.
    """
    key = id(root)
    if key not in _PARENT_MAP_CACHE:
        _PARENT_MAP_CACHE[key] = {id(c): p for p in root.iter() for c in p}
    return _PARENT_MAP_CACHE[key]


# Internal alias kept for the patch engine's existing call sites.
_parent_map = parent_map


def invalidate_parent_map(root: ElementTree.Element) -> None:
    _PARENT_MAP_CACHE.pop(id(root), None)


def _ancestors(root, element):
    """Yield element then each ancestor up to (but not including) root, in order."""
    yield element
    pmap = _parent_map(root)
    current = element
    while id(current) in pmap:
        current = pmap[id(current)]
        if current is root:
            break
        yield current


def _nearest_ancestor_id(root, element):
    for anc in _ancestors(root, element):
        v = anc.get('id') or anc.get('name')
        if v:
            return v
    return None


def _do_remove(root, selector, silent, warnings) -> dict[str, list[str]]:
    try:
        targets = xpath_find(root, selector)
    except XPathError:
        raise _OpError('unsupported_xpath', selector)
    touched: dict[str, list[str]] = {}
    if not targets:
        if silent:
            warnings.append((
                f'silent remove target not found sel={selector}',
                {'reason': 'silent_remove_miss', 'sel': selector,
                 'affected_keys': _infer_affected_keys(selector)},
            ))
            return touched
        raise _OpError('remove_target_missing', selector)
    invalidate_parent_map(root)
    for t in targets:
        if isinstance(t, AttrRef):
            if t.name in t.parent.attrib:
                del t.parent.attrib[t.name]
            entity_id, _ = _ancestor_entity_and_ref_path(root, t.parent, selector)
            if entity_id:
                touched.setdefault(entity_id, [])
        else:
            parent = _parent_of(root, t)
            if parent is not None:
                entity_id = t.get('id') or t.get('name') or _nearest_ancestor_id(root, parent)
                parent.remove(t)
                if entity_id:
                    touched.setdefault(entity_id, [])
    return touched


def _parent_of(root, element):
    return _parent_map(root).get(id(element))


def _clone(element):
    new = ElementTree.Element(element.tag, attrib=dict(element.attrib))
    new.text, new.tail = element.text, element.tail
    for c in element:
        new.append(_clone(c))
    return new


@dataclass
class EntityRecord:
    key: Hashable
    element: ElementTree.Element
    source_files: list[str]
    sources: list[str]
    ref_sources: dict[str, str]


@dataclass
class ModifiedRecord:
    key: Hashable
    old: ElementTree.Element
    new: ElementTree.Element
    old_source_files: list[str]
    new_source_files: list[str]
    old_sources: list[str]
    new_sources: list[str]
    old_ref_sources: dict[str, str]
    new_ref_sources: dict[str, str]


@dataclass
class DiffReport:
    added: list[EntityRecord]
    removed: list[EntityRecord]
    modified: list[ModifiedRecord]
    effective_old_root: Optional[ElementTree.Element] = None
    effective_new_root: Optional[ElementTree.Element] = None
    warnings: list[tuple[str, dict]] = field(default_factory=list)
    failures: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


def diff_library(old_root: Path, new_root: Path,
                 file_rel: str, entity_xpath: str,
                 key_fn: Optional[Callable[[ElementTree.Element], Hashable]] = None,
                 key_fn_identity: str = '',
                 include_dlc: bool = True) -> DiffReport:
    key = (
        str(old_root.resolve()), str(new_root.resolve()),
        file_rel, entity_xpath,
        key_fn_identity or ('id(' + hex(id(key_fn)) + ')' if key_fn else 'default_id'),
        include_dlc,
    )
    def produce():
        return _diff_library_impl(old_root, new_root, file_rel, entity_xpath,
                                  key_fn or (lambda e: e.get('id')), include_dlc)
    return cache.get_or_compute(key, produce)


def _diff_library_impl(old_root, new_root, file_rel, entity_xpath, key_fn, include_dlc):
    old_tree, old_contribs, old_ref_sources, old_warnings, old_failures = _materialize(
        old_root, file_rel, include_dlc)
    new_tree, new_contribs, new_ref_sources, new_warnings, new_failures = _materialize(
        new_root, file_rel, include_dlc)
    old_map = _index_by_key(old_tree, entity_xpath, key_fn)
    new_map = _index_by_key(new_tree, entity_xpath, key_fn)
    added, removed, modified = [], [], []
    for k in new_map.keys() - old_map.keys():
        element = new_map[k]
        src_files, sources_label, refs = _provenance(element, k, new_contribs, new_ref_sources)
        added.append(EntityRecord(k, element, src_files, sources_label, refs))
    for k in old_map.keys() - new_map.keys():
        element = old_map[k]
        src_files, sources_label, refs = _provenance(element, k, old_contribs, old_ref_sources)
        removed.append(EntityRecord(k, element, src_files, sources_label, refs))
    for k in old_map.keys() & new_map.keys():
        old_el, new_el = old_map[k], new_map[k]
        if _element_equal(old_el, new_el):
            continue
        o_files, o_srcs, o_refs = _provenance(old_el, k, old_contribs, old_ref_sources)
        n_files, n_srcs, n_refs = _provenance(new_el, k, new_contribs, new_ref_sources)
        modified.append(ModifiedRecord(k, old_el, new_el,
                                       o_files, n_files, o_srcs, n_srcs,
                                       o_refs, n_refs))
    return DiffReport(
        added=added, removed=removed, modified=modified,
        effective_old_root=old_tree,
        effective_new_root=new_tree,
        warnings=list(old_warnings) + list(new_warnings),
        failures=list(old_failures) + list(new_failures),
    )


def _materialize(root: Path, file_rel: str, include_dlc: bool):
    """Return (effective_tree_root, contrib_map, ref_sources_map, warnings, failures)."""
    core = root / file_rel
    contribs: dict[Hashable, list[tuple[str, str]]] = {}
    ref_sources: dict[Hashable, dict[str, str]] = {}
    warnings: list[tuple[str, dict]] = []
    failures: list[tuple[str, dict]] = []
    if core.exists():
        eff = ElementTree.parse(core).getroot()
        _seed_sources_from_tree(eff, file_rel, 'core', contribs, ref_sources)
    else:
        eff = ElementTree.fromstring('<root/>')
    if include_dlc:
        dlc_files = sorted((root / 'extensions').glob('*/' + file_rel))
        parsed: list[tuple[str, str, ElementTree.Element]] = []
        for dlc_file in dlc_files:
            rel = str(dlc_file.relative_to(root))
            short = source_of(rel)
            try:
                parsed.append((rel, short, ElementTree.parse(dlc_file).getroot()))
            except ElementTree.ParseError as e:
                failures.append((f'parse error {rel}', {'reason': 'parse_error',
                                                        'detail': str(e),
                                                        'affected_keys': []}))
        per_dlc_ops = []
        for rel, short, dlc_root in parsed:
            operations = list(dlc_root) if dlc_root.tag == 'diff' \
                  else [_synthesize_add(c, dlc_root.tag) for c in dlc_root]
            per_dlc_ops.append((rel, short, operations))
        # Pre-flight: snapshot core tree for gate evaluation and sel resolution.
        pre_dlc_root = _deepcopy_tree(eff)
        ws_with_gates = []
        for rel, short, operations in per_dlc_ops:
            w_list = _write_set(operations)
            gate_flags = []
            for operation in operations:
                gate = operation.get('if')
                if not gate:
                    gate_flags.append(True)
                    continue
                try:
                    gate_flags.append(_eval_if(pre_dlc_root, gate))
                except _OpError:
                    gate_flags.append(True)
            ws_with_gates.append((short, w_list, gate_flags))
        rs = [(short, _read_set(operations)) for _, short, operations in per_dlc_ops]
        for kind, text, extras in _classify_conflicts(pre_dlc_root, ws_with_gates, rs):
            if kind == 'warning':
                warnings.append((text, extras))
            else:
                failures.append((text, extras))
        for kind, text, extras in _detect_raw_conflicts(pre_dlc_root, per_dlc_ops):
            if kind == 'warning':
                warnings.append((text, extras))
            else:
                failures.append((text, extras))
        # Apply each DLC's ops in discovered (alphabetical via sorted glob) order.
        for rel, short, operations in per_dlc_ops:
            for operation in operations:
                try:
                    actually_touched = _apply_op(eff, operation, warnings)
                except _Skip:
                    continue
                except (XPathError, _OpError) as e:
                    failures.append((f'patch op failed: {operation.tag} sel={operation.get("sel")}', {
                        'reason': getattr(e, 'reason', 'unknown'),
                        'op_tag': operation.tag, 'sel': operation.get('sel'),
                        'if': operation.get('if'), 'detail': str(e),
                        'affected_keys': _infer_affected_keys(operation.get('sel')),
                    }))
                    continue
                for entity_id, ref_paths in actually_touched.items():
                    ct = (rel, short)
                    if ct not in contribs.setdefault(entity_id, []):
                        contribs[entity_id].append(ct)
                    r = ref_sources.setdefault(entity_id, {})
                    for rp in ref_paths:
                        r[rp] = short
    return eff, contribs, ref_sources, warnings, failures


def _seed_sources_from_tree(eff: ElementTree.Element, file_rel: str, short: str,
                             contribs: dict, ref_sources: dict) -> None:
    """Initial contributor/ref-source attribution for the core tree."""
    for element in eff.iter():
        entity_id = element.get('id') or element.get('name')
        if entity_id is None:
            continue
        contribs.setdefault(entity_id, []).append((file_rel, short))
        for ref_child, attribute in [('component', 'ref')]:
            child = element.find(ref_child)
            if child is not None and child.get(attribute) is not None:
                ref_sources.setdefault(entity_id, {})[f'{ref_child}/@{attribute}'] = short


def _index_by_key(tree_root: ElementTree.Element, entity_xpath: str,
                  key_fn: Callable[[ElementTree.Element], Hashable]) -> dict:
    """Contract: entity_xpath is one of `//<tag>`, `.//<tag>`, or `<tag>`.
    Rules needing richer selection filter inside key_fn.
    """
    m = re.match(r'^\.?\/\/?(\w+)$', entity_xpath.strip())
    if not m:
        raise XPathError(
            f'entity_xpath must be //<tag>, .//<tag>, or <tag>; got {entity_xpath!r}')
    tag = m.group(1)
    out: dict = {}
    for element in tree_root.iter(tag):
        k = key_fn(element)
        if k is None:
            continue
        out[k] = element
    return out


def _provenance(element: ElementTree.Element, key, contribs, ref_sources_map):
    """Contributor set + per-reference writer attribution for one entity."""
    idk = element.get('id') or element.get('name')
    if idk is None:
        idk = key if isinstance(key, str) else repr(key)
    entries = contribs.get(idk, [])
    if not entries:
        entries = [('', 'core')]
    src_files = sorted({f for f, _ in entries if f})
    sources_label = sorted({s for _, s in entries})
    refs = dict(ref_sources_map.get(idk, {}))
    return src_files, sources_label, refs


def _element_equal(a, b) -> bool:
    if a.tag != b.tag or a.attrib != b.attrib:
        return False
    if (a.text or '').strip() != (b.text or '').strip():
        return False
    if len(a) != len(b):
        return False
    return all(_element_equal(x, y) for x, y in zip(a, b))


@dataclass
class WriteOp:
    selector: str
    op_kind: str
    attr_name: Optional[str]
    position: Optional[str]
    body_digest: Optional[str]
    added_child_ids: list[str]


def _write_set(operations: list[ElementTree.Element]) -> list[WriteOp]:
    """Per-op write-set record. body_digest distinguishes 'same body dedupe' from
    'different bodies FAILURE'; added_child_ids drives id-collision detection."""
    out: list[WriteOp] = []
    for operation in operations:
        selector = operation.get('sel') or ''
        attr_name = None
        m = re.search(r'/@(' + _NAME + r')\s*$', selector)
        if m:
            attr_name = m.group(1)
        body_digest = None
        if operation.tag == 'replace':
            if attr_name is not None:
                body_digest = sha256((operation.text or '').strip().encode('utf-8')).hexdigest()[:16]
            elif len(operation):
                body_digest = sha256(ElementTree.tostring(operation[0])).hexdigest()[:16]
        added_child_ids: list[str] = []
        if operation.tag == 'add':
            for c in operation:
                cid = c.get('id') or c.get('name')
                if cid:
                    added_child_ids.append(cid)
        out.append(WriteOp(
            selector=_norm_sel(selector), op_kind=operation.tag, attr_name=attr_name,
            position=operation.get('pos'), body_digest=body_digest,
            added_child_ids=added_child_ids,
        ))
    return out


def _norm_sel(selector: str) -> str:
    """Textual normalization - use `_resolve_sel_to_targets` for identity."""
    s = selector.strip()
    if s.startswith('./'):
        s = s[1:]
    return s


def _resolve_sel_to_targets(core_root: ElementTree.Element, selector: str,
                             attr_name: Optional[str]) -> frozenset:
    """Resolve sel to a canonical frozenset of (element_id, attr_name) tuples.

    Two syntactic selectors that resolve to the same element bucket together.
    Unresolvable sels -> empty frozenset (caller falls back to string key).
    """
    try:
        matches = xpath_find(core_root, selector)
    except XPathError:
        return frozenset()
    out = set()
    for m in matches:
        if isinstance(m, AttrRef):
            out.add((id(m.parent), m.name))
        else:
            out.add((id(m), attr_name))
    return frozenset(out)


def _read_set(operations: list[ElementTree.Element]) -> list[str]:
    """xpaths the ops READ via `if=` gates.

    Only `if=` conditions are true reads whose truth value depends on prior
    writes. The op's own `sel` is its write target, not a read — same-sel
    collisions are handled by the conflict-classification rules in
    `_classify_conflicts`; positional anchors (after/before/prepend) get
    their own rule.
    """
    out = []
    for operation in operations:
        gate = operation.get('if')
        if gate:
            m = re.match(r'^\s*not\s*\((.+)\)\s*$', gate)
            out.append(m.group(1).strip() if m else gate)
    return out


def _classify_conflicts(core_root: ElementTree.Element,
                        per_dlc_ws: list[tuple[str, list[WriteOp], list[bool]]],
                        per_dlc_rs: list[tuple[str, list[str]]]
                        ) -> list[tuple[str, str, dict]]:
    """Cross-DLC classification. Returns list of (kind, text, extras) where
    kind in {'failure', 'warning'}.

    Rules:
    1. Same target replace/remove, different bodies -> FAILURE.
    2. Same-parent add with colliding @id -> FAILURE.
    3. Element-level remove/replace vs nested op from another DLC -> FAILURE.
    4. Same (target, position) add from multiple DLCs -> WARNING (if no id collision).
    5. if= read-after-write across DLCs -> FAILURE.
    """
    out: list[tuple[str, str, dict]] = []
    all_writes: list[tuple[str, WriteOp]] = []
    for short, whitespace, gate_flags in per_dlc_ws:
        for w, gate_passed in zip(whitespace, gate_flags):
            if gate_passed:
                all_writes.append((short, w))

    def _canon_target(w: WriteOp) -> frozenset:
        return _resolve_sel_to_targets(core_root, w.selector, w.attr_name)

    # Rule 1: write-write on same target, different bodies.
    by_target: dict[frozenset, list[tuple[str, WriteOp]]] = {}
    for short, w in all_writes:
        if w.op_kind in ('replace', 'remove'):
            tgt = _canon_target(w)
            if not tgt:
                tgt = frozenset({('_unresolved_', _norm_sel(w.selector))})
            by_target.setdefault(tgt, []).append((short, w))
    for tgt, entries in by_target.items():
        dlcs = {e[0] for e in entries}
        if len(dlcs) < 2:
            continue
        selector = entries[0][1].selector
        attribute = entries[0][1].attr_name
        digests = {e[1].body_digest for e in entries if e[1].op_kind == 'replace'}
        has_remove = any(e[1].op_kind == 'remove' for e in entries)
        if (len([d for d in digests if d is not None]) > 1) or (has_remove and digests):
            out.append(('failure',
                        f'write-write conflict on {selector} attribute={attribute}',
                        {'reason': 'write_write_conflict',
                         'sel': selector, 'attribute': attribute,
                         'dlcs': sorted(dlcs),
                         'affected_keys': _infer_affected_keys(selector)}))

    # Rule 2: add/add id collision.
    add_by_parent: dict[frozenset, list[tuple[str, WriteOp]]] = {}
    for short, w in all_writes:
        if w.op_kind == 'add':
            tgt = _canon_target(w)
            if not tgt:
                tgt = frozenset({('_unresolved_', _norm_sel(w.selector))})
            add_by_parent.setdefault(tgt, []).append((short, w))
    for parent_tgt, entries in add_by_parent.items():
        selector = entries[0][1].selector
        seen_ids: dict[str, str] = {}
        for short, w in entries:
            for cid in w.added_child_ids:
                if cid in seen_ids and seen_ids[cid] != short:
                    out.append(('failure',
                                f'add id collision on {selector}: id={cid}',
                                {'reason': 'add_id_collision',
                                 'sel': selector, 'colliding_id': cid,
                                 'dlcs': sorted({seen_ids[cid], short}),
                                 'affected_keys': [cid]}))
                seen_ids[cid] = short

    # Rule 3: subtree invalidation.
    elem_removes_replaces = [(s, w) for s, w in all_writes
                             if w.op_kind in ('remove', 'replace')
                             and w.attr_name is None]
    for short_a, w_a in elem_removes_replaces:
        for short_b, w_b in all_writes:
            if short_a == short_b or w_a is w_b:
                continue
            if w_b.selector.startswith(w_a.selector + '/') or (
                    w_b.selector != w_a.selector and w_b.selector.startswith(w_a.selector)):
                out.append(('failure',
                            f'subtree invalidation: {short_a} {w_a.op_kind} {w_a.selector} vs {short_b} {w_b.op_kind} {w_b.selector}',
                            {'reason': 'subtree_invalidation',
                             'outer_sel': w_a.selector, 'inner_sel': w_b.selector,
                             'dlcs': sorted({short_a, short_b}),
                             'affected_keys': _infer_affected_keys(w_a.selector)}))

    # Rule 4: positional overlap WARNING (skip if any id collision).
    pos_by_key: dict[tuple[frozenset, str], list[tuple[str, WriteOp]]] = {}
    for short, w in all_writes:
        if w.op_kind == 'add' and w.position in ('after', 'prepend', 'before'):
            tgt = _canon_target(w)
            if not tgt:
                tgt = frozenset({('_unresolved_', _norm_sel(w.selector))})
            pos_by_key.setdefault((tgt, w.position), []).append((short, w))
    for (tgt, position), entries in pos_by_key.items():
        uniq_dlcs = sorted({e[0] for e in entries})
        if len(uniq_dlcs) < 2:
            continue
        ids_by_dlc: dict[str, set[str]] = {}
        for short, w in entries:
            ids_by_dlc.setdefault(short, set()).update(w.added_child_ids)
        collided = False
        dlcs_list = list(ids_by_dlc.keys())
        for i in range(len(dlcs_list)):
            for j in range(i + 1, len(dlcs_list)):
                if ids_by_dlc[dlcs_list[i]] & ids_by_dlc[dlcs_list[j]]:
                    collided = True
                    break
            if collided:
                break
        if collided:
            continue
        selector = entries[0][1].selector
        out.append(('warning',
                    f'positional overlap position={position} on {selector}',
                    {'reason': 'positional_overlap',
                     'sel': selector, 'position': position,
                     'dlcs': uniq_dlcs,
                     'affected_keys': _infer_affected_keys(selector)}))

    # Rule 5: if= read-after-write.
    write_targets_by_dlc: dict[str, set[str]] = {}
    for short, w in all_writes:
        write_targets_by_dlc.setdefault(short, set()).add(w.selector)
    for short_r, reads in per_dlc_rs:
        for read_xp in reads:
            for short_w, write_targets in write_targets_by_dlc.items():
                if short_w == short_r:
                    continue
                for wt in write_targets:
                    if wt == read_xp or wt.startswith(read_xp + '/') or \
                       read_xp.startswith(wt + '/'):
                        out.append(('failure',
                                    f'read-after-write: {short_r} reads {read_xp}, {short_w} writes {wt}',
                                    {'reason': 'if_raw_dependency',
                                     'read': read_xp, 'write': wt,
                                     'dlcs': sorted({short_r, short_w}),
                                     'affected_keys': _infer_affected_keys(read_xp)}))

    return out


def _detect_raw_conflicts(pre_dlc_root: ElementTree.Element,
                          per_dlc_ops: list[tuple[str, str, list[ElementTree.Element]]]
                          ) -> list[tuple[str, str, dict]]:
    """Semantic RAW detection: would applying another DLC's ops flip this
    DLC's if= gates? If yes, FAILURE.

    Complements `_classify_conflicts` Rule 5 which only catches string-matched
    gate-vs-write intersections (e.g., one DLC's gate reads the same xpath
    another DLC removes). This catches the harder case where one DLC's WRITE
    introduces an element the other DLC's gate tested for absence of.
    """
    out: list[tuple[str, str, dict]] = []
    # Precompute each DLC's if= gates as (op, gate_xp).
    gates_by_dlc: dict[str, list[tuple[ElementTree.Element, str]]] = {}
    for rel, short, operations in per_dlc_ops:
        for operation in operations:
            gate = operation.get('if')
            if gate:
                gates_by_dlc.setdefault(short, []).append((operation, gate))
    if not gates_by_dlc:
        return out
    for rel_a, short_a, _ops_a in per_dlc_ops:
        a_gates = gates_by_dlc.get(short_a, [])
        if not a_gates:
            continue
        # Baseline: evaluate A's gates against pre-DLC tree.
        baselines = []
        for op_a, gate_xp in a_gates:
            try:
                baselines.append((op_a, gate_xp, _eval_if(pre_dlc_root, gate_xp)))
            except _OpError:
                # Unparseable gate — can't classify; apply path will surface the
                # real failure.
                baselines.append((op_a, gate_xp, None))
        for rel_b, short_b, ops_b in per_dlc_ops:
            if short_a == short_b:
                continue
            hypothetical = _deepcopy_tree(pre_dlc_root)
            for op_b in ops_b:
                try:
                    _apply_op(hypothetical, op_b, [])
                except (_Skip, XPathError, _OpError):
                    continue
            for op_a, gate_xp, pre_val in baselines:
                if pre_val is None:
                    continue
                try:
                    post_val = _eval_if(hypothetical, gate_xp)
                except _OpError:
                    continue
                if pre_val != post_val:
                    selector = op_a.get('sel') or ''
                    out.append((
                        'failure',
                        f'read-after-write: {short_a} if="{gate_xp}" flips after {short_b} applies',
                        {'reason': 'if_raw_gate_flip',
                         'read': gate_xp,
                         'gate_dlc': short_a,
                         'writer_dlc': short_b,
                         'dlcs': sorted({short_a, short_b}),
                         'sel': selector,
                         'affected_keys': _infer_affected_keys(gate_xp) or _infer_affected_keys(selector)},
                    ))
    return out


def _deepcopy_tree(element: ElementTree.Element) -> ElementTree.Element:
    """Deep-copy an ET tree (shallow `.copy()` wouldn't duplicate children)."""
    return ElementTree.fromstring(ElementTree.tostring(element))
