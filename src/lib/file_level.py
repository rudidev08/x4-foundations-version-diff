"""File-level diff helpers. Used by rules that work at file granularity
(quests, gamelogic/aiscripts).
"""
import difflib
from pathlib import Path
from typing import Optional

from src.change_map import ChangeKind

_DIFF_BYTES_CAP = 100 * 1024
_DIFF_LINES_CAP = 5000
_HEAD_BUDGET = 40 * 1024
_TAIL_BUDGET = 20 * 1024


def diff_files(old_root: Path, new_root: Path, globs: list[str]
               ) -> list[tuple[str, ChangeKind, Optional[bytes], Optional[bytes]]]:
    out: list = []
    old_map: dict[str, Path] = {}
    new_map: dict[str, Path] = {}
    for g in globs:
        for p in old_root.glob(g):
            if p.is_file():
                old_map[str(p.relative_to(old_root))] = p
        for p in new_root.glob(g):
            if p.is_file():
                new_map[str(p.relative_to(new_root))] = p
    for rel in sorted(old_map.keys() - new_map.keys()):
        out.append((rel, ChangeKind.DELETED, old_map[rel].read_bytes(), None))
    for rel in sorted(new_map.keys() - old_map.keys()):
        out.append((rel, ChangeKind.ADDED, None, new_map[rel].read_bytes()))
    for rel in sorted(old_map.keys() & new_map.keys()):
        ob, nb = old_map[rel].read_bytes(), new_map[rel].read_bytes()
        if ob != nb:
            out.append((rel, ChangeKind.MODIFIED, ob, nb))
    return out


def render_modified(rel: str, old: Optional[bytes], new: Optional[bytes],
                    tag: str, name: str) -> tuple[str, dict]:
    """Render a file-level change. old=None → added file; new=None → removed file.

    Truncation slices at line boundaries on decoded text so repeated calls
    produce identical bytes (snapshot stability).
    """
    old_bytes = old if old is not None else b''
    new_bytes = new if new is not None else b''
    old_lines = old_bytes.decode('utf-8', 'replace').splitlines(keepends=True)
    new_lines = new_bytes.decode('utf-8', 'replace').splitlines(keepends=True)
    diff_list = list(difflib.unified_diff(old_lines, new_lines, fromfile=rel, tofile=rel))
    added = sum(1 for l in diff_list if l.startswith('+') and not l.startswith('+++'))
    removed = sum(1 for l in diff_list if l.startswith('-') and not l.startswith('---'))
    diff_text = ''.join(diff_list)
    truncated = False
    total_bytes = len(diff_text.encode('utf-8'))
    total_lines = diff_text.count('\n')
    if total_bytes > _DIFF_BYTES_CAP or total_lines > _DIFF_LINES_CAP:
        truncated = True
        head = _head_by_budget(diff_list, _HEAD_BUDGET)
        tail = _tail_by_budget(diff_list, _TAIL_BUDGET)
        diff_text = head + f'\n... [hunks truncated, {total_bytes} total bytes] ...\n' + tail
    if old is None and new is not None:
        summary = f'ADDED (+{added} lines)'
    elif new is None and old is not None:
        summary = f'REMOVED (-{removed} lines)'
    else:
        summary = f'modified (+{added}/-{removed} lines)'
    text = f'[{tag}] {name}: {summary}'
    extras = {
        'path': rel, 'diff': diff_text,
        'added_lines': added, 'removed_lines': removed,
        'total_added_lines': added, 'total_removed_lines': removed,
        'diff_truncated': truncated,
    }
    return text, extras


def _head_by_budget(lines: list[str], budget_bytes: int) -> str:
    out = []
    used = 0
    for line in lines:
        b = len(line.encode('utf-8'))
        if used + b > budget_bytes and out:
            break
        out.append(line)
        used += b
    return ''.join(out)


def _tail_by_budget(lines: list[str], budget_bytes: int) -> str:
    out: list[str] = []
    used = 0
    for line in reversed(lines):
        b = len(line.encode('utf-8'))
        if used + b > budget_bytes and out:
            break
        out.insert(0, line)
        used += b
    return ''.join(out)
