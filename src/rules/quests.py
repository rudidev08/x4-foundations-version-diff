"""Quests rule: emit one row per md/*.xml mdscript change at file granularity.

File-level rule: each distinct rel path (core or DLC) is its own entity. There
is no filename-based override — X4's md/ tree is additive, so `md/foo.xml` and
`extensions/ego_dlc_boron/md/foo.xml` surface as TWO independent rows.

Render uses `diff_files` + `render_modified` from `src.lib.file_level`. Added
and removed files round-trip through `render_modified` with an empty bytes
side so the unified diff shows the full content with `+`/`-` prefixes; the
summary text is then swapped to the `ADDED (+N lines)` / `REMOVED (-N lines)`
form the plan specifies.

Classifications come from the filename prefix (chars before the first `_`).
Literal bare filenames (e.g., `notifications`) take precedence over prefix
matching. Unknown prefixes yield an empty classification list — explicit
empty keeps snapshots from thrashing as new prefixes appear.
"""
from pathlib import Path
import xml.etree.ElementTree as ElementTree

from src.change_map import ChangeKind
from src.lib.file_level import diff_files, render_modified
from src.lib.paths import source_of
from src.lib.rule_output import RuleOutput


TAG = 'quests'

# Prefix → classification token. Prefix is the chars before the first `_`.
# Extended from real-data inventory of 9.00B6 md/ trees (core + all DLCs).
# Prefixes seen ≥5 times are mapped; rarer ones fall through to empty list.
_CLASS_MAP = {
    'gm': 'generic_mission',
    'story': 'story',
    'factionlogic': 'factionlogic',
    'scenario': 'scenario',
    'gs': 'gamestart',
    'trade': 'trade',
    'rml': 'rml',                       # Reusable mission library
    'tutorial': 'tutorial',
    'setup': 'setup',
    'npc': 'npc',
    'cm': 'combat',                     # combat manager / content manager
    'x4ep1': 'timelines',               # Timelines DLC (X4 Expansion Pack 1)
    'lib': 'library',                   # shared mdscript helpers
    'gmc': 'gm_canned',                 # canned generic missions
    'factiongoal': 'factiongoal',
    'factionsubgoal': 'factionsubgoal',
    'terraforming': 'terraforming',
    'inituniverse': 'universe_init',
    'cinematiccamera': 'cinematic',
}

# Literal bare-filename matches take precedence over prefix mapping.
_LITERAL_CLASS = {
    'notifications': 'notification',
}

# Generic-token filter (empty — classifications are emitted verbatim).
def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit one RuleOutput per file-level change across md/*.xml trees.

    `changes` is accepted for uniform rule interface; the rule scans directly.
    """
    outputs: list[RuleOutput] = []
    results = diff_files(
        old_root, new_root,
        globs=['md/*.xml', 'extensions/*/md/*.xml'],
    )
    for rel, kind, old_bytes, new_bytes in results:
        outputs.extend(_emit(rel, kind, old_bytes, new_bytes))
    return outputs


def _emit(rel: str, kind: ChangeKind,
          old: bytes | None, new: bytes | None) -> list[RuleOutput]:
    name = _display_name(rel, new if new is not None else old)
    classifications = _classifications(rel)
    source = source_of(rel)
    if kind == ChangeKind.ADDED:
        _, extras = render_modified(rel, b'', new, tag=TAG, name=name)
        added = extras['added_lines']
        text = f'[{TAG}] {name}: ADDED (+{added} lines)'
        extras.update({
            'entity_key': rel,
            'kind': 'added',
            'sources': [source],
            'source_files': [rel],
            'classifications': classifications,
        })
        return [RuleOutput(tag=TAG, text=text, extras=extras)]
    if kind == ChangeKind.DELETED:
        _, extras = render_modified(rel, old, b'', tag=TAG, name=name)
        removed = extras['removed_lines']
        text = f'[{TAG}] {name}: REMOVED (-{removed} lines)'
        extras.update({
            'entity_key': rel,
            'kind': 'removed',
            'sources': [source],
            'source_files': [rel],
            'classifications': classifications,
        })
        return [RuleOutput(tag=TAG, text=text, extras=extras)]
    # MODIFIED
    text, extras = render_modified(
        rel, old, new, tag=TAG, name=name,
    )
    extras.update({
        'entity_key': rel,
        'kind': 'modified',
        'sources': [source],
        'source_files': [rel],
        'classifications': classifications,
    })
    return [RuleOutput(tag=TAG, text=text, extras=extras)]


def _display_name(rel: str, xml_bytes: bytes | None) -> str:
    """`<mdscript @name>` if parseable, otherwise filename stem.

    Non-UTF-8 / malformed XML falls through cleanly; the stem is stable even
    for garbage bytes so we never crash the rule on a single bad file.
    """
    if xml_bytes:
        try:
            root = ElementTree.fromstring(xml_bytes)
            if root.tag == 'mdscript' and root.get('name'):
                return root.get('name')
        except ElementTree.ParseError:
            pass
    return Path(rel).stem


def _classifications(rel: str) -> list[str]:
    """Filename-prefix based classification list.

    1. Literal stem match wins (e.g., `notifications` → `['notification']`).
    2. Otherwise, chars before first `_` map via `_CLASS_MAP`.
    3. For no-underscore names, the whole stem is the prefix.
    4. Unknown prefix → empty list (explicit; no fallback token).
    """
    stem = Path(rel).stem
    if stem in _LITERAL_CLASS:
        return [_LITERAL_CLASS[stem]]
    prefix = stem.split('_', 1)[0] if '_' in stem else stem
    token = _CLASS_MAP.get(prefix)
    if token is None:
        return []
    return [token]
