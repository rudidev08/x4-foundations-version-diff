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
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Iterable, Optional

REF = re.compile(r'\{(\d+),(\d+)\}')
_AUTHOR_HINT = re.compile(r'^\([^)]*\)')

_CORE_REL = Path('t/0001-l044.xml')
_DLC_PATTERN = 'extensions/*/t/0001-l044.xml'

_BUILD_CACHE: dict[str, 'Locale'] = {}


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
            root = ElementTree.parse(Path(path)).getroot()
            entries = {}
            collisions = []
            for page in root.findall('page'):
                page_id = int(page.get('id'))
                for t in page.findall('t'):
                    entries[(page_id, int(t.get('id')))] = t.text or ''
        self._entries: dict[tuple[int, int], str] = entries or {}
        self.collisions: list[tuple[str, dict]] = collisions or []

    @classmethod
    def build_pair(cls, old_root: Path, new_root: Path, outputs: list,
                   tag: str) -> tuple['Locale', 'Locale']:
        """Build both side Locales and forward new-side collisions as warnings.
        The common 3-line preamble for every rule that needs both Locales.
        """
        from src.lib.check_incomplete import forward_warnings
        locale_old = cls.build(old_root)
        locale_new = cls.build(new_root)
        forward_warnings(locale_new.collisions, outputs, tag=tag)
        return locale_old, locale_new

    @classmethod
    def build(cls, root: Path) -> 'Locale':
        # Memoize on the resolved root path: every rule calls Locale.build
        # for both old and new roots, and parsing core + DLC overlays runs
        # ~6 MB of XML each time. Cache makes this once-per-(root, run).
        key = str(root.resolve())
        cached = _BUILD_CACHE.get(key)
        if cached is not None:
            return cached
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
        result = cls(entries=entries, collisions=collisions)
        _BUILD_CACHE[key] = result
        return result

    def get(self, page: int, text_id: int, _depth: int = 10) -> str:
        raw = self._entries.get((page, text_id))
        if raw is None:
            return f'{{{page},{text_id}}}'
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
    root = ElementTree.parse(path).getroot()
    for page in root.findall('page'):
        page_id = int(page.get('id'))
        for t in page.findall('t'):
            key = (page_id, int(t.get('id')))
            text = t.text or ''
            if key in entries and dlc_name != 'core':
                prev_src, prev_text = sources[key]
                if prev_text != text:
                    collisions.append((
                        f'locale collision page={page_id} id={key[1]}',
                        {
                            'page': page_id, 'id': key[1],
                            'core_text': prev_text if prev_src == 'core' else None,
                            'dlc_text': text,
                            'dlc_name': dlc_name,
                            'previous_source': prev_src,
                        },
                    ))
            entries[key] = text
            sources[key] = (dlc_name, text)


def resolve_attr_ref(element: ElementTree.Element, locale: Locale, attribute: str = 'name',
                     fallback: Optional[str] = None) -> str:
    """Parse {page,id} from any attribute on element; resolve via locale.

    Falls back to `fallback` when:
    - element is None
    - attribute is missing
    - attribute value matches {page,id} but locale has no entry.
    Otherwise returns the attribute value verbatim (strips author hints if it looks
    like resolved text).
    """
    if element is None:
        return fallback if fallback is not None else ''
    raw = element.get(attribute)
    if raw is None:
        return fallback if fallback is not None else ''
    m = REF.fullmatch(raw)
    if not m:
        return raw
    resolved = locale.get(int(m[1]), int(m[2]))
    if resolved == raw:  # unchanged = miss
        return fallback if fallback is not None else raw
    return _AUTHOR_HINT.sub('', resolved, count=1).strip()


def display_name(macro: ElementTree.Element, locale: Locale) -> str:
    """Resolve a macro's display name via properties/identification/@name."""
    ident = macro.find('properties/identification')
    if ident is None:
        return macro.get('name', 'unknown')
    return resolve_attr_ref(ident, locale, attribute='name', fallback=macro.get('name', 'unknown'))


