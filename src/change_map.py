"""Scan two source trees, emit an index of changed files.

File-level granularity only; rules subdivide per-entity as needed.
"""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ChangeKind(str, Enum):
    ADDED = 'added'
    MODIFIED = 'modified'
    DELETED = 'deleted'


@dataclass(frozen=True)
class FileChange:
    path: str
    kind: ChangeKind


def build(old_root: Path, new_root: Path) -> list[FileChange]:
    old_map = {str(p.relative_to(old_root)): p for p in old_root.rglob('*') if p.is_file()}
    new_map = {str(p.relative_to(new_root)): p for p in new_root.rglob('*') if p.is_file()}
    changes: list[FileChange] = []
    for rel in sorted(old_map.keys() - new_map.keys()):
        changes.append(FileChange(rel, ChangeKind.DELETED))
    for rel in sorted(new_map.keys() - old_map.keys()):
        changes.append(FileChange(rel, ChangeKind.ADDED))
    for rel in sorted(old_map.keys() & new_map.keys()):
        old_p = old_map[rel]
        new_p = new_map[rel]
        # Cheap size-mismatch shortcut: skips the byte read for the
        # >90% of files whose sizes differ when modified.
        if old_p.stat().st_size != new_p.stat().st_size:
            changes.append(FileChange(rel, ChangeKind.MODIFIED))
            continue
        if old_p.read_bytes() != new_p.read_bytes():
            changes.append(FileChange(rel, ChangeKind.MODIFIED))
    return changes
