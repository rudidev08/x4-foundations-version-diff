"""Entity-level diff helper for X4 libraries/maps.

This module grows across Gates 0b and 0c:
- 0b.2: XPath subset evaluator.
- 0b.3: patch engine (single-version materialization) + DiffReport skeleton.
- 0c.*: conflict classification + provenance + contaminated-output propagation.
"""
import re
import xml.etree.ElementTree as ET
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
    parent: ET.Element
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


def xpath_find(root: ET.Element, xpath: str,
               document_root: Optional[ET.Element] = None
               ) -> list[Union[ET.Element, AttrRef]]:
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


def _find_elems(root: ET.Element, xp: str,
                document_root: Optional[ET.Element] = None) -> list[ET.Element]:
    doc = document_root if document_root is not None else root
    steps = _parse_steps(xp)
    if not steps:
        return []
    absolute = steps[0][3]
    current: list[ET.Element] = [root]
    first = True
    for step in steps:
        axis, tag, preds, _abs = step
        # Split predicates: literal-integer positionals filter the set per
        # parent AFTER the boolean predicates; everything else is per-element.
        bool_preds = [p for p in preds if not _POS_PRED_RE.match(p)]
        pos_preds = [int(_POS_PRED_RE.match(p).group(1))
                     for p in preds if _POS_PRED_RE.match(p)]
        nxt: list[ET.Element] = []
        for cur in current:
            if first and absolute:
                cands = [cur] if cur.tag == tag else []
            elif axis == '//':
                cands = list(cur.iter(tag))
                if cands and cands[0] is cur:
                    cands = cands[1:]
            else:
                cands = [c for c in cur if c.tag == tag]
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
    pos = 0
    axis = leading
    first = True
    while pos < len(xp):
        m = re.match(_NAME, xp[pos:])
        if not m:
            raise XPathError(f'unexpected at pos {pos}: {xp[pos:]}')
        tag = m.group(0)
        pos += len(tag)
        preds: list[str] = []
        while pos < len(xp) and xp[pos] == '[':
            depth = 1
            end = pos + 1
            while end < len(xp) and depth:
                if xp[end] == '[':
                    depth += 1
                elif xp[end] == ']':
                    depth -= 1
                end += 1
            body = xp[pos + 1:end - 1]
            _validate_pred(body)
            preds.append(body)
            pos = end
        out.append((axis, tag, preds, absolute_first and first))
        first = False
        if pos < len(xp):
            if xp[pos:pos + 2] == '//':
                axis = '//'
                pos += 2
            elif xp[pos] == '/':
                axis = '/'
                pos += 1
            else:
                raise XPathError(f'unexpected at pos {pos}: {xp[pos:]}')
    return out


_EQ_PRED = re.compile(r"^\s*@(" + _NAME + r")\s*=\s*'([^']*)'\s*$")
_NOT_PRED = re.compile(r"^\s*not\s*\((.+)\)\s*$")


def _eval_pred(elem: ET.Element, pred: str,
               document_root: Optional[ET.Element] = None) -> bool:
    pred = pred.strip()
    m = _EQ_PRED.match(pred)
    if m:
        return elem.get(m.group(1)) == m.group(2)
    m = _NOT_PRED.match(pred)
    if m:
        return not _eval_bare(elem, m.group(1).strip(), document_root)
    return _eval_bare(elem, pred, document_root)


def _eval_bare(elem: ET.Element, bare: str,
               document_root: Optional[ET.Element] = None) -> bool:
    """Evaluate a bare XPath as truthy when matches exist.

    Handles: `//descendant` (anchored to document_root, NOT elem's subtree),
    `/child`, `tag`, `tag/child`, `tag[@attr='v']`, nested `not(...)`, and
    predicates on descendants. `//` MUST search from document_root — threading
    it through is what prevents `[not(//faction[...])]` on a job from
    incorrectly searching only the job's subtree.
    """
    probe = bare.strip()
    if probe.startswith('//'):
        # // anchors to the document root. Use document_root, not elem.
        anchor = document_root if document_root is not None else elem
        return bool(_find_elems(anchor, probe, document_root))
    elif probe.startswith('/'):
        anchor = document_root if document_root is not None else elem
        return bool(_find_elems(anchor, probe, document_root))
    else:
        # Bare path `tag` or `tag/child` is CHILD-axis per XPath semantics.
        # `[not(factions)]` on <location> = no direct child factions (not
        # descendant). The parser's "no leading slash" branch is exactly
        # child-axis-from-elem — pass the probe as-is.
        return bool(_find_elems(elem, probe, document_root))


def apply_patch(effective_root: ET.Element,
                patch_root: ET.Element
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
            ops = list(patch_root)
        else:
            # Native-fragment: wrap each top-level child as <add sel="/<file-root>">child</add>.
            # <file-root> is the DLC file's root tag — same as the core file's root
            # (e.g., <plans>, <loadouts>) — NOT the child tag.
            fragment_root_tag = patch_root.tag
            ops = [_synthesize_add(c, fragment_root_tag) for c in patch_root]
        for op in ops:
            try:
                _apply_op(effective_root, op, warnings)
            except _Skip:
                pass
            except (XPathError, _OpError) as e:
                failures.append((f'patch op failed: {op.tag} sel={op.get("sel")}', {
                    'reason': e.reason if hasattr(e, 'reason') else 'unknown',
                    'op_tag': op.tag,
                    'sel': op.get('sel'),
                    'if': op.get('if'),
                    'detail': str(e),
                    'affected_keys': _infer_affected_keys(op.get('sel')),
                }))
        return failures, warnings
    finally:
        invalidate_parent_map(effective_root)


def _infer_affected_keys(sel: Optional[str]) -> list:
    """Best-effort extract entity keys from a selector.

    `//ware[@id='X']` → `['X']`. Both single and double quotes match —
    X4 timelines DLC escapes predicates with `&quot;`, which ElementTree
    decodes to double quotes. Composite-keyed rules (Wave 2+) inject their
    own key extraction via the entity index; this fallback is for the `@id='X'`
    majority case. Selectors that don't pin an entity yield an empty list,
    which makes forward_incomplete mark ALL outputs from that sub-report.
    """
    if not sel:
        return []
    m = _KEY_PRED_RE.search(sel)
    if m:
        return [m.group(2)]
    return []


class _Skip(Exception):
    pass


class _OpError(Exception):
    def __init__(self, reason, detail=''):
        super().__init__(detail)
        self.reason = reason


def _synthesize_add(child: ET.Element, file_root_tag: str) -> ET.Element:
    """Wrap a native-fragment child as <add sel="/<file-root>">child</add>.

    file_root_tag comes from the DLC file's root element (e.g. 'plans',
    'loadouts', 'ships') — same as the core file's root. This matches the
    effective tree's shape, NOT the child's tag.
    """
    op = ET.Element('add', attrib={'sel': '/' + file_root_tag})
    op.append(child)
    return op


def _apply_op(root: ET.Element, op: ET.Element,
              warnings: list[tuple[str, dict]]) -> dict[str, list[str]]:
    """Apply op to root. Returns {entity_id: [ref_paths_written]} for entities
    this op actually mutated. Empty dict if op was skipped, failed, or touched
    no id-bearing entities.

    Callers use the return value for provenance attribution so we only record
    contributors for ops that ran successfully — NOT preflight-scanned ops.
    """
    if op.get('if'):
        if not _eval_if(root, op.get('if')):
            raise _Skip()
    sel = op.get('sel')
    if op.tag == 'add':
        pos = op.get('pos')
        return _do_add(root, sel, list(op), pos)
    elif op.tag == 'replace':
        return _do_replace(root, sel, op)
    elif op.tag == 'remove':
        silent = op.get('silent') in ('true', '1')
        return _do_remove(root, sel, silent, warnings)
    else:
        raise _OpError('unknown_op', f'op={op.tag}')


def _eval_if(root, xp):
    """Evaluate `if=` gate. Supports `not(XPATH)` top-level wrapping AND bare XPath.

    `if="not(//faction[@id='terran'])"` is the spec's canonical form (line 108);
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


def _do_add(root, sel, children, pos) -> dict[str, list[str]]:
    try:
        targets = xpath_find(root, sel)
    except XPathError:
        raise _OpError('unsupported_xpath', sel)
    if not targets:
        raise _OpError('add_target_missing', sel)
    touched: dict[str, list[str]] = {}
    invalidate_parent_map(root)  # parent relationships change after insert
    for t in targets:
        if isinstance(t, AttrRef):
            raise _OpError('add_to_attr', sel)
        if pos == 'prepend':
            for i, c in enumerate(children):
                t.insert(i, _clone(c))
        elif pos == 'after':
            parent = _parent_of(root, t)
            if parent is None:
                raise _OpError('no_parent_for_after', sel)
            idx = list(parent).index(t) + 1
            for c in children:
                parent.insert(idx, _clone(c))
                idx += 1
        elif pos == 'before':
            parent = _parent_of(root, t)
            if parent is None:
                raise _OpError('no_parent_for_before', sel)
            idx = list(parent).index(t)
            for c in children:
                parent.insert(idx, _clone(c))
                idx += 1
        else:
            for c in children:
                t.append(_clone(c))
        # Attribute inserted children with any id/name they carry, and record any
        # ref-bearing child under them. Currently `<component ref="...">` only; other
        # ref-carrying forms will be generalized in a later refactor when the set
        # expands.
        for c in children:
            entity_id = c.get('id') or c.get('name')
            if entity_id:
                touched.setdefault(entity_id, [])
                for ref_child, attr in [('component', 'ref')]:
                    inner = c.find(ref_child)
                    if inner is not None and inner.get(attr) is not None:
                        touched[entity_id].append(f'{ref_child}/@{attr}')
    return touched


def _do_replace(root, sel, op) -> dict[str, list[str]]:
    try:
        targets = xpath_find(root, sel)
    except XPathError:
        raise _OpError('unsupported_xpath', sel)
    if not targets:
        raise _OpError('replace_target_missing', sel)
    touched: dict[str, list[str]] = {}
    invalidate_parent_map(root)
    for t in targets:
        if isinstance(t, AttrRef):
            t.parent.set(t.name, (op.text or '').strip())
            # Walk up to the nearest entity-bearing ancestor.
            entity_id, ref_path = _ancestor_entity_and_ref_path(root, t.parent, sel)
            if entity_id:
                touched.setdefault(entity_id, [])
                if ref_path:
                    touched[entity_id].append(ref_path)
        else:
            new_el = list(op)[0] if len(op) else None
            if new_el is None:
                raise _OpError('replace_body_missing', sel)
            parent = _parent_of(root, t)
            if parent is None:
                raise _OpError('no_parent_for_replace', sel)
            idx = list(parent).index(t)
            parent.remove(t)
            parent.insert(idx, _clone(new_el))
            # Attribute to nearest id-bearing ancestor or the replaced element's id.
            entity_id = new_el.get('id') or new_el.get('name') or \
                        _nearest_ancestor_id(root, parent)
            if entity_id:
                touched.setdefault(entity_id, [])
    return touched


def _ancestor_entity_and_ref_path(root, parent_elem, sel):
    """Walk up from parent_elem to find an id/name bearing ancestor; return
    (entity_id, ref_path) where ref_path is the element/@attr tail of sel.
    """
    m = re.search(r'/(\w+)/@(\w+)\s*$', sel or '')
    ref_path = f'{m.group(1)}/@{m.group(2)}' if m else None
    # Search ancestors for id/name.
    for anc in _ancestors(root, parent_elem):
        if anc.get('id') or anc.get('name'):
            return (anc.get('id') or anc.get('name'), ref_path)
    return (None, ref_path)


# Parent-map cache: keyed by id(root) so one apply_patch or _materialize call
# builds the map once and reuses it across every _ancestors / _parent_of call.
# Cleared automatically when the tree is mutated (cleared via invalidate_parent_map).
_PARENT_MAP_CACHE: dict[int, dict[int, ET.Element]] = {}


def _parent_map(root: ET.Element) -> dict[int, ET.Element]:
    key = id(root)
    if key not in _PARENT_MAP_CACHE:
        _PARENT_MAP_CACHE[key] = {id(c): p for p in root.iter() for c in p}
    return _PARENT_MAP_CACHE[key]


def invalidate_parent_map(root: ET.Element) -> None:
    _PARENT_MAP_CACHE.pop(id(root), None)


def _ancestors(root, elem):
    """Yield elem then each ancestor up to (but not including) root, in order."""
    yield elem
    pmap = _parent_map(root)
    cur = elem
    while id(cur) in pmap:
        cur = pmap[id(cur)]
        if cur is root:
            break
        yield cur


def _nearest_ancestor_id(root, elem):
    for anc in _ancestors(root, elem):
        v = anc.get('id') or anc.get('name')
        if v:
            return v
    return None


def _do_remove(root, sel, silent, warnings) -> dict[str, list[str]]:
    try:
        targets = xpath_find(root, sel)
    except XPathError:
        raise _OpError('unsupported_xpath', sel)
    touched: dict[str, list[str]] = {}
    if not targets:
        if silent:
            warnings.append((
                f'silent remove target not found sel={sel}',
                {'reason': 'silent_remove_miss', 'sel': sel,
                 'affected_keys': _infer_affected_keys(sel)},
            ))
            return touched
        raise _OpError('remove_target_missing', sel)
    invalidate_parent_map(root)
    for t in targets:
        if isinstance(t, AttrRef):
            if t.name in t.parent.attrib:
                del t.parent.attrib[t.name]
            entity_id, _ = _ancestor_entity_and_ref_path(root, t.parent, sel)
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


def _parent_of(root, elem):
    return _parent_map(root).get(id(elem))


def _clone(el):
    new = ET.Element(el.tag, attrib=dict(el.attrib))
    new.text, new.tail = el.text, el.tail
    for c in el:
        new.append(_clone(c))
    return new


@dataclass
class EntityRecord:
    key: Hashable
    element: ET.Element
    source_files: list[str]
    sources: list[str]
    ref_sources: dict[str, str]


@dataclass
class ModifiedRecord:
    key: Hashable
    old: ET.Element
    new: ET.Element
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
    effective_old_root: Optional[ET.Element] = None
    effective_new_root: Optional[ET.Element] = None
    warnings: list[tuple[str, dict]] = field(default_factory=list)
    failures: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


def diff_library(old_root: Path, new_root: Path,
                 file_rel: str, entity_xpath: str,
                 key_fn: Optional[Callable[[ET.Element], Hashable]] = None,
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
        el = new_map[k]
        src_files, srcs, refs = _provenance(el, k, new_contribs, new_ref_sources)
        added.append(EntityRecord(k, el, src_files, srcs, refs))
    for k in old_map.keys() - new_map.keys():
        el = old_map[k]
        src_files, srcs, refs = _provenance(el, k, old_contribs, old_ref_sources)
        removed.append(EntityRecord(k, el, src_files, srcs, refs))
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
        eff = ET.parse(core).getroot()
        _seed_sources_from_tree(eff, file_rel, 'core', contribs, ref_sources)
    else:
        eff = ET.fromstring('<root/>')
    if include_dlc:
        dlc_files = sorted((root / 'extensions').glob('*/' + file_rel))
        parsed: list[tuple[str, str, ET.Element]] = []
        for dlc_file in dlc_files:
            rel = str(dlc_file.relative_to(root))
            short = source_of(rel)
            try:
                parsed.append((rel, short, ET.parse(dlc_file).getroot()))
            except ET.ParseError as e:
                failures.append((f'parse error {rel}', {'reason': 'parse_error',
                                                        'detail': str(e),
                                                        'affected_keys': []}))
        per_dlc_ops = []
        for rel, short, dlc_root in parsed:
            ops = list(dlc_root) if dlc_root.tag == 'diff' \
                  else [_synthesize_add(c, dlc_root.tag) for c in dlc_root]
            per_dlc_ops.append((rel, short, ops))
        # Pre-flight: snapshot core tree for gate evaluation and sel resolution.
        pre_dlc_root = _deepcopy_tree(eff)
        ws_with_gates = []
        for rel, short, ops in per_dlc_ops:
            w_list = _write_set(ops)
            gate_flags = []
            for op in ops:
                gate = op.get('if')
                if not gate:
                    gate_flags.append(True)
                    continue
                try:
                    gate_flags.append(_eval_if(pre_dlc_root, gate))
                except _OpError:
                    gate_flags.append(True)
            ws_with_gates.append((short, w_list, gate_flags))
        rs = [(short, _read_set(ops)) for _, short, ops in per_dlc_ops]
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
        for rel, short, ops in per_dlc_ops:
            for op in ops:
                try:
                    actually_touched = _apply_op(eff, op, warnings)
                except _Skip:
                    continue
                except (XPathError, _OpError) as e:
                    failures.append((f'patch op failed: {op.tag} sel={op.get("sel")}', {
                        'reason': getattr(e, 'reason', 'unknown'),
                        'op_tag': op.tag, 'sel': op.get('sel'),
                        'if': op.get('if'), 'detail': str(e),
                        'affected_keys': _infer_affected_keys(op.get('sel')),
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


def _seed_sources_from_tree(eff: ET.Element, file_rel: str, short: str,
                             contribs: dict, ref_sources: dict) -> None:
    """Initial contributor/ref-source attribution for the core tree."""
    for el in eff.iter():
        entity_id = el.get('id') or el.get('name')
        if entity_id is None:
            continue
        contribs.setdefault(entity_id, []).append((file_rel, short))
        for ref_child, attr in [('component', 'ref')]:
            child = el.find(ref_child)
            if child is not None and child.get(attr) is not None:
                ref_sources.setdefault(entity_id, {})[f'{ref_child}/@{attr}'] = short


def _index_by_key(tree_root: ET.Element, entity_xpath: str,
                  key_fn: Callable[[ET.Element], Hashable]) -> dict:
    """Contract: entity_xpath is one of `//<tag>`, `.//<tag>`, or `<tag>`.
    Rules needing richer selection filter inside key_fn.
    """
    m = re.match(r'^\.?\/\/?(\w+)$', entity_xpath.strip())
    if not m:
        raise XPathError(
            f'entity_xpath must be //<tag>, .//<tag>, or <tag>; got {entity_xpath!r}')
    tag = m.group(1)
    out: dict = {}
    for el in tree_root.iter(tag):
        k = key_fn(el)
        if k is None:
            continue
        out[k] = el
    return out


def _provenance(el: ET.Element, key, contribs, ref_sources_map):
    """Contributor set + per-reference writer attribution for one entity."""
    idk = el.get('id') or el.get('name')
    if idk is None:
        idk = key if isinstance(key, str) else repr(key)
    entries = contribs.get(idk, [])
    if not entries:
        entries = [('', 'core')]
    src_files = sorted({f for f, _ in entries if f})
    srcs = sorted({s for _, s in entries})
    refs = dict(ref_sources_map.get(idk, {}))
    return src_files, srcs, refs


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
    sel: str
    op_kind: str
    attr_name: Optional[str]
    pos: Optional[str]
    body_digest: Optional[str]
    added_child_ids: list[str]


def _write_set(ops: list[ET.Element]) -> list[WriteOp]:
    """Per-op write-set record. body_digest distinguishes 'same body dedupe' from
    'different bodies FAILURE'; added_child_ids drives id-collision detection."""
    out: list[WriteOp] = []
    for op in ops:
        sel = op.get('sel') or ''
        attr_name = None
        m = re.search(r'/@(' + _NAME + r')\s*$', sel)
        if m:
            attr_name = m.group(1)
        body_digest = None
        if op.tag == 'replace':
            if attr_name is not None:
                body_digest = sha256((op.text or '').strip().encode('utf-8')).hexdigest()[:16]
            elif len(op):
                body_digest = sha256(ET.tostring(op[0])).hexdigest()[:16]
        added_child_ids: list[str] = []
        if op.tag == 'add':
            for c in op:
                cid = c.get('id') or c.get('name')
                if cid:
                    added_child_ids.append(cid)
        out.append(WriteOp(
            sel=_norm_sel(sel), op_kind=op.tag, attr_name=attr_name,
            pos=op.get('pos'), body_digest=body_digest,
            added_child_ids=added_child_ids,
        ))
    return out


def _norm_sel(sel: str) -> str:
    """Textual normalization - use `_resolve_sel_to_targets` for identity."""
    s = sel.strip()
    if s.startswith('./'):
        s = s[1:]
    return s


def _resolve_sel_to_targets(core_root: ET.Element, sel: str,
                             attr_name: Optional[str]) -> frozenset:
    """Resolve sel to a canonical frozenset of (element_id, attr_name) tuples.

    Two syntactic selectors that resolve to the same element bucket together.
    Unresolvable sels -> empty frozenset (caller falls back to string key).
    """
    try:
        matches = xpath_find(core_root, sel)
    except XPathError:
        return frozenset()
    out = set()
    for m in matches:
        if isinstance(m, AttrRef):
            out.add((id(m.parent), m.name))
        else:
            out.add((id(m), attr_name))
    return frozenset(out)


def _read_set(ops: list[ET.Element]) -> list[str]:
    """xpaths the ops READ via `if=` gates.

    Only `if=` conditions are true reads whose truth value depends on prior
    writes. The op's own `sel` is its write target, not a read — same-sel
    collisions are handled by Rules 1/2/3/4. 0c.3 extends anchor detection
    (pos=after/before/prepend) as a separate rule.
    """
    out = []
    for op in ops:
        gate = op.get('if')
        if gate:
            m = re.match(r'^\s*not\s*\((.+)\)\s*$', gate)
            out.append(m.group(1).strip() if m else gate)
    return out


def _classify_conflicts(core_root: ET.Element,
                        per_dlc_ws: list[tuple[str, list[WriteOp], list[bool]]],
                        per_dlc_rs: list[tuple[str, list[str]]]
                        ) -> list[tuple[str, str, dict]]:
    """Cross-DLC classification. Returns list of (kind, text, extras) where
    kind in {'failure', 'warning'}.

    Rules:
    1. Same target replace/remove, different bodies -> FAILURE.
    2. Same-parent add with colliding @id -> FAILURE.
    3. Element-level remove/replace vs nested op from another DLC -> FAILURE.
    4. Same (target, pos) add from multiple DLCs -> WARNING (if no id collision).
    5. if= read-after-write across DLCs -> FAILURE.
    """
    out: list[tuple[str, str, dict]] = []
    all_writes: list[tuple[str, WriteOp]] = []
    for short, ws, gate_flags in per_dlc_ws:
        for w, gate_passed in zip(ws, gate_flags):
            if gate_passed:
                all_writes.append((short, w))

    def _canon_target(w: WriteOp) -> frozenset:
        return _resolve_sel_to_targets(core_root, w.sel, w.attr_name)

    # Rule 1: write-write on same target, different bodies.
    by_target: dict[frozenset, list[tuple[str, WriteOp]]] = {}
    for short, w in all_writes:
        if w.op_kind in ('replace', 'remove'):
            tgt = _canon_target(w)
            if not tgt:
                tgt = frozenset({('_unresolved_', _norm_sel(w.sel))})
            by_target.setdefault(tgt, []).append((short, w))
    for tgt, entries in by_target.items():
        dlcs = {e[0] for e in entries}
        if len(dlcs) < 2:
            continue
        sel = entries[0][1].sel
        attr = entries[0][1].attr_name
        digests = {e[1].body_digest for e in entries if e[1].op_kind == 'replace'}
        has_remove = any(e[1].op_kind == 'remove' for e in entries)
        if (len([d for d in digests if d is not None]) > 1) or (has_remove and digests):
            out.append(('failure',
                        f'write-write conflict on {sel} attr={attr}',
                        {'reason': 'write_write_conflict',
                         'sel': sel, 'attr': attr,
                         'dlcs': sorted(dlcs),
                         'affected_keys': _infer_affected_keys(sel)}))

    # Rule 2: add/add id collision.
    add_by_parent: dict[frozenset, list[tuple[str, WriteOp]]] = {}
    for short, w in all_writes:
        if w.op_kind == 'add':
            tgt = _canon_target(w)
            if not tgt:
                tgt = frozenset({('_unresolved_', _norm_sel(w.sel))})
            add_by_parent.setdefault(tgt, []).append((short, w))
    for parent_tgt, entries in add_by_parent.items():
        sel = entries[0][1].sel
        seen_ids: dict[str, str] = {}
        for short, w in entries:
            for cid in w.added_child_ids:
                if cid in seen_ids and seen_ids[cid] != short:
                    out.append(('failure',
                                f'add id collision on {sel}: id={cid}',
                                {'reason': 'add_id_collision',
                                 'sel': sel, 'colliding_id': cid,
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
            if w_b.sel.startswith(w_a.sel + '/') or (
                    w_b.sel != w_a.sel and w_b.sel.startswith(w_a.sel)):
                out.append(('failure',
                            f'subtree invalidation: {short_a} {w_a.op_kind} {w_a.sel} vs {short_b} {w_b.op_kind} {w_b.sel}',
                            {'reason': 'subtree_invalidation',
                             'outer_sel': w_a.sel, 'inner_sel': w_b.sel,
                             'dlcs': sorted({short_a, short_b}),
                             'affected_keys': _infer_affected_keys(w_a.sel)}))

    # Rule 4: positional overlap WARNING (skip if any id collision).
    pos_by_key: dict[tuple[frozenset, str], list[tuple[str, WriteOp]]] = {}
    for short, w in all_writes:
        if w.op_kind == 'add' and w.pos in ('after', 'prepend', 'before'):
            tgt = _canon_target(w)
            if not tgt:
                tgt = frozenset({('_unresolved_', _norm_sel(w.sel))})
            pos_by_key.setdefault((tgt, w.pos), []).append((short, w))
    for (tgt, pos), entries in pos_by_key.items():
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
        sel = entries[0][1].sel
        out.append(('warning',
                    f'positional overlap pos={pos} on {sel}',
                    {'reason': 'positional_overlap',
                     'sel': sel, 'pos': pos,
                     'dlcs': uniq_dlcs,
                     'affected_keys': _infer_affected_keys(sel)}))

    # Rule 5: if= read-after-write.
    write_targets_by_dlc: dict[str, set[str]] = {}
    for short, w in all_writes:
        write_targets_by_dlc.setdefault(short, set()).add(w.sel)
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


def _detect_raw_conflicts(pre_dlc_root: ET.Element,
                          per_dlc_ops: list[tuple[str, str, list[ET.Element]]]
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
    gates_by_dlc: dict[str, list[tuple[ET.Element, str]]] = {}
    for rel, short, ops in per_dlc_ops:
        for op in ops:
            gate = op.get('if')
            if gate:
                gates_by_dlc.setdefault(short, []).append((op, gate))
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
                    sel = op_a.get('sel') or ''
                    out.append((
                        'failure',
                        f'read-after-write: {short_a} if="{gate_xp}" flips after {short_b} applies',
                        {'reason': 'if_raw_gate_flip',
                         'read': gate_xp,
                         'gate_dlc': short_a,
                         'writer_dlc': short_b,
                         'dlcs': sorted({short_a, short_b}),
                         'sel': sel,
                         'affected_keys': _infer_affected_keys(gate_xp) or _infer_affected_keys(sel)},
                    ))
    return out


def _deepcopy_tree(elem: ET.Element) -> ET.Element:
    """Deep-copy an ET tree (shallow `.copy()` wouldn't duplicate children)."""
    return ET.fromstring(ET.tostring(elem))
