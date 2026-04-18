"""Resolve X4 locale refs like {20106,2024} to plain English text.

Locale entries may contain:
- A leading author-hint in parentheses, e.g. `(TEL M Shield Generator Mk1)...` —
  an editor comment, stripped before display.
- Nested {page,id} refs that recursively substitute.

Multi-DLC: `Locale.build(root)` globs `extensions/*/t/0001-l044.xml` onto core
`t/0001-l044.xml`. Merge order alphabetical by DLC directory name (stability
heuristic; X4's real load order is content.xml-driven and isn't preserved in
extracted data). DLC entries override core on same (page, id); overrides are
recorded in `locale.collisions` as warning-shaped tuples.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional

REF = re.compile(r'\{(\d+),(\d+)\}')
_AUTHOR_HINT = re.compile(r'^\([^)]*\)')

_CORE_REL = Path('t/0001-l044.xml')
_DLC_PATTERN = 'extensions/*/t/0001-l044.xml'


class Locale:
    """Locale entries for one effective version (core + optional DLC merge).

    Two constructors:
    - Locale(path)          — single-file load; back-compat for shields/missiles.
    - Locale.build(root)    — DLC glob-merge; used by new rules.
    Collisions tracked on either path.
    """
    def __init__(self, path=None, *, entries=None, collisions=None):
        if path is not None:
            # Single-file load (back-compat).
            root = ET.parse(Path(path)).getroot()
            entries = {}
            collisions = []
            for page in root.findall('page'):
                pid = int(page.get('id'))
                for t in page.findall('t'):
                    entries[(pid, int(t.get('id')))] = t.text or ''
        self._entries: dict[tuple[int, int], str] = entries or {}
        self.collisions: list[tuple[str, dict]] = collisions or []

    @classmethod
    def build(cls, root: Path) -> 'Locale':
        entries: dict[tuple[int, int], str] = {}
        sources: dict[tuple[int, int], tuple[str, str]] = {}  # (page,id) → (dlc_name, text)
        collisions: list[tuple[str, dict]] = []

        core_path = root / _CORE_REL
        if core_path.exists():
            _ingest(core_path, 'core', entries, sources, collisions)

        dlc_paths = sorted(root.glob(_DLC_PATTERN))  # alphabetical stability
        for p in dlc_paths:
            dlc_dir = p.parts[-3]  # extensions/<dlc>/t/0001-l044.xml → <dlc>
            dlc_name = dlc_dir[len('ego_dlc_'):] if dlc_dir.startswith('ego_dlc_') else dlc_dir
            _ingest(p, dlc_name, entries, sources, collisions)
        return cls(entries=entries, collisions=collisions)

    def get(self, page: int, tid: int, _depth: int = 10) -> str:
        raw = self._entries.get((page, tid))
        if raw is None:
            return f'{{{page},{tid}}}'
        text = _AUTHOR_HINT.sub('', raw, count=1)
        if _depth <= 0:
            return text
        return REF.sub(lambda m: self.get(int(m[1]), int(m[2]), _depth - 1), text)

    def resolve(self, ref: str) -> str:
        m = REF.fullmatch(ref)
        if not m:
            return ref
        return self.get(int(m[1]), int(m[2]))


def _ingest(path: Path, dlc_name: str,
            entries: dict[tuple[int, int], str],
            sources: dict[tuple[int, int], tuple[str, str]],
            collisions: list[tuple[str, dict]]) -> None:
    root = ET.parse(path).getroot()
    for page in root.findall('page'):
        pid = int(page.get('id'))
        for t in page.findall('t'):
            key = (pid, int(t.get('id')))
            text = t.text or ''
            if key in entries and dlc_name != 'core':
                prev_src, prev_text = sources[key]
                if prev_text != text:
                    collisions.append((
                        f'locale collision page={pid} id={key[1]}',
                        {
                            'page': pid, 'id': key[1],
                            'core_text': prev_text if prev_src == 'core' else None,
                            'dlc_text': text,
                            'dlc_name': dlc_name,
                            'previous_source': prev_src,
                        },
                    ))
            entries[key] = text
            sources[key] = (dlc_name, text)


def resolve_attr_ref(elem: ET.Element, locale: Locale, attr: str = 'name',
                     fallback: Optional[str] = None) -> str:
    """Parse {page,id} from any attribute on elem; resolve via locale.

    Falls back to `fallback` when:
    - elem is None
    - attr is missing
    - attr value matches {page,id} but locale has no entry.
    Otherwise returns the attr value verbatim (strips author hints if it looks
    like resolved text).
    """
    if elem is None:
        return fallback if fallback is not None else ''
    raw = elem.get(attr)
    if raw is None:
        return fallback if fallback is not None else ''
    m = REF.fullmatch(raw)
    if not m:
        return raw
    resolved = locale.get(int(m[1]), int(m[2]))
    if resolved == raw:  # unchanged = miss
        return fallback if fallback is not None else raw
    return _AUTHOR_HINT.sub('', resolved, count=1).strip()


def display_name(macro: ET.Element, locale: Locale) -> str:
    """Resolve a macro's display name via properties/identification/@name."""
    ident = macro.find('properties/identification')
    if ident is None:
        return macro.get('name', 'unknown')
    return resolve_attr_ref(ident, locale, attr='name', fallback=macro.get('name', 'unknown'))
