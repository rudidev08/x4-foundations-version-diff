"""Storage rule: emit outputs for storage-module macro changes.

Storage modules live under `{core|extensions/*/}assets/props/StorageModules/`
(DLC variants sometimes lowercase `storagemodules/`). Each storage macro defines
cargo capacity, accepted cargo tags (container / liquid / solid / ...), and
integrated hull. There's no ware entry and no per-macro locale — `@name` on the
macro element is the only stable identifier.

Parent-ship hint: ship macros reference storage via nested
`<connections>/<connection>/<macro ref="storage_..."/>`. The rule builds a
reverse index across ALL ship macros on the relevant side (old for removed,
new for added/modified) so unchanged ships still appear as parents of changed
storage.
"""
from pathlib import Path
from typing import Iterable, Optional
import xml.etree.ElementTree as ElementTree

from src.change_map import ChangeKind
from src.lib import cache
from src.lib.file_level import diff_files
from src.lib.macro_diff import diff_attrs
from src.lib.paths import source_of
from src.lib.rule_output import RuleOutput, format_row, render_sources


TAG = 'storage'

# Macro stat spec: (xpath_under_macro, attribute, label).
MACRO_STATS = [
    ('properties/cargo', 'max', 'cargo_max'),
    ('properties/cargo', 'tags', 'cargo_tags'),
    ('properties/hull', 'integrated', 'hull_integrated'),
]

_GLOBS = [
    'assets/props/StorageModules/macros/storage_*_macro.xml',
    'assets/props/storagemodules/macros/storage_*_macro.xml',
    'extensions/*/assets/props/StorageModules/macros/storage_*_macro.xml',
    'extensions/*/assets/props/storagemodules/macros/storage_*_macro.xml',
]


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit storage rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused — the rule drives itself
    off file-level diffs across the storage macro globs.
    """
    outputs: list[RuleOutput] = []
    results = diff_files(old_root, new_root, globs=_GLOBS)
    # Case-insensitive filesystems (macOS default) resolve both
    # `StorageModules/` and `storagemodules/` globs to the same on-disk
    # files, producing distinct relative paths for identical inodes. Dedupe
    # by resolved path so each storage macro file emits at most one row.
    for rel, kind, old_bytes, new_bytes in _dedupe_by_inode(
            results, old_root, new_root):
        outputs.extend(_emit_one(rel, kind, old_bytes, new_bytes,
                                 old_root, new_root))
    return outputs


def _dedupe_by_inode(results, old_root: Path, new_root: Path):
    """Drop diff_files entries whose on-disk inode matches a previously-seen
    one. On case-insensitive filesystems (macOS APFS, Windows) the
    `StorageModules/` and `storagemodules/` globs both resolve to the same
    inode but produce distinct rel paths — keep the first casing seen."""
    seen: set = set()
    for rel, kind, old_bytes, new_bytes in results:
        key_parts: list = []
        if old_bytes is not None:
            st = (old_root / rel).stat()
            key_parts.append(('old', st.st_dev, st.st_ino))
        if new_bytes is not None:
            st = (new_root / rel).stat()
            key_parts.append(('new', st.st_dev, st.st_ino))
        key = tuple(key_parts) or ('rel', rel)
        if key in seen:
            continue
        seen.add(key)
        yield rel, kind, old_bytes, new_bytes


def _emit_one(rel: str, kind: ChangeKind,
              old_bytes: Optional[bytes], new_bytes: Optional[bytes],
              old_root: Path, new_root: Path) -> list[RuleOutput]:
    if kind == ChangeKind.ADDED:
        macro = _parse_macro(new_bytes)
        if macro is None:
            return []
        return [_build_row(macro, rel, 'added',
                           old_root, new_root, side='new', changes=[])]
    if kind == ChangeKind.DELETED:
        macro = _parse_macro(old_bytes)
        if macro is None:
            return []
        return [_build_row(macro, rel, 'removed',
                           old_root, new_root, side='old', changes=[])]
    # MODIFIED
    old_macro = _parse_macro(old_bytes)
    new_macro = _parse_macro(new_bytes)
    if old_macro is None or new_macro is None:
        return []
    changes: list[str] = []
    for label, (old_value, new_value) in diff_attrs(old_macro, new_macro, MACRO_STATS).items():
        changes.append(f'{label} {old_value}→{new_value}')
    if not changes:
        return []
    return [_build_row(new_macro, rel, 'modified',
                       old_root, new_root, side='new', changes=changes)]


def _build_row(macro: ElementTree.Element, rel: str, kind: str,
               old_root: Path, new_root: Path,
               side: str, changes: list[str]) -> RuleOutput:
    macro_name = macro.get('name') or Path(rel).stem
    classifications = _classify(macro)
    source = source_of(rel)

    tree_root = new_root if side == 'new' else old_root
    parents = sorted(_parent_ship_index(side, tree_root).get(macro_name, []))

    extras: dict = {
        'entity_key': macro_name,
        'macro': macro_name,
        'kind': kind,
        'classifications': classifications,
        'source': source,
    }
    if len(parents) == 1:
        extras['parent_ship'] = parents[0]
    elif len(parents) >= 2:
        extras['parent_ships'] = parents

    # Provenance rendering: single-source rows always carry exactly one source
    # (add → new side only, remove → old side only, modified → new side only).
    if kind == 'added':
        sources_label = render_sources(None, [source])
    elif kind == 'removed':
        sources_label = render_sources([source], None)
    else:  # modified
        sources_label = render_sources([source], [source])

    if kind == 'added':
        parts = ['NEW'] + changes if changes else ['NEW']
    elif kind == 'removed':
        parts = ['REMOVED']
    else:
        parts = list(changes)

    text = format_row(TAG, macro_name, classifications, sources_label, parts)
    return RuleOutput(tag=TAG, text=text, extras=extras)


def _parse_macro(data: bytes) -> Optional[ElementTree.Element]:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None
    return root.find('macro')


def _classify(macro: ElementTree.Element) -> list[str]:
    """Classifications from cargo @tags — whitespace-split, no generic filter.

    E.g. `<cargo tags="container">` → `['container']`; `<cargo tags="solid">`
    → `['solid']`. Empty / missing yields an empty list.
    """
    cargo = macro.find('properties/cargo')
    if cargo is None:
        return []
    tags = (cargo.get('tags') or '').split()
    return [t for t in tags if t]


def _parent_ship_index(side: str, tree_root: Path) -> dict[str, list[str]]:
    """Return `{storage_macro_ref: [ship_macro_display_name, ...]}`.

    Indexes every ship macro under `assets/units/**` (core) and
    `extensions/*/assets/units/**` (DLC). Ship macros reference storage via
    nested `<connections>/<connection>/<macro ref="storage_..."/>` — NOT via
    `<connection @ref>` (that's the connection schema anchor, not the storage
    ref). Cache key is `('storage_parent_ship_index', side, resolved_root)` so
    old/new trees don't collide.
    """
    cache_key = ('storage_parent_ship_index', side, str(tree_root.resolve()))

    def produce() -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for ship_macro_path in _iter_ship_macros(tree_root):
            try:
                doc = ElementTree.parse(ship_macro_path).getroot()
            except (FileNotFoundError, ElementTree.ParseError):
                continue
            macro = doc.find('macro')
            if macro is None:
                continue
            ship_name = macro.get('name') or ship_macro_path.stem
            for conn in macro.iter('connection'):
                for nested in conn.findall('macro'):
                    ref = nested.get('ref')
                    if ref and ref.startswith('storage_'):
                        index.setdefault(ref, []).append(ship_name)
        # Dedupe ship names per storage ref (same ship referencing the same
        # storage from two connections shouldn't inflate the parent count).
        return {k: sorted(set(v)) for k, v in index.items()}

    return cache.get_or_compute(cache_key, produce)


def _iter_ship_macros(tree_root: Path) -> Iterable[Path]:
    yield from tree_root.glob('assets/units/**/ship_*_macro.xml')
    yield from tree_root.glob('extensions/*/assets/units/**/ship_*_macro.xml')
