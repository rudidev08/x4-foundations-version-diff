#!/usr/bin/env python3
"""Generate unified diffs between two X4 source versions.

Usage:
    python3 diff-tools/version_diff.py 8.00H4 9.00B1

Output goes to diff/{V1}-{V2}/ with per-file .diff files.
"""

from __future__ import annotations

import argparse
import difflib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "source"
DIFF_DIR = ROOT / "diff"

TEXT_EXTENSIONS = {".xml", ".xsd", ".lua", ".html", ".css", ".js", ".txt", ".md", ".json", ".cfg"}


def collect_files(base: Path) -> set[str]:
    """Return set of relative file paths under base."""
    result = set()
    for root, _, files in os.walk(base):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), base)
            result.add(rel)
    return result


def read_lines(path: Path) -> list[str] | None:
    """Read file lines, returning None for binary files."""
    try:
        with open(path, encoding="utf-8", errors="strict") as f:
            return f.readlines()
    except (UnicodeDecodeError, ValueError):
        return None


def make_diff(old_path: Path, new_path: Path, rel_path: str,
              old_label: str, new_label: str) -> str | None:
    """Generate unified diff between two files. Returns None if identical or binary."""
    old_lines = read_lines(old_path) if old_path.exists() else []
    new_lines = read_lines(new_path) if new_path.exists() else []

    if old_lines is None or new_lines is None:
        return None

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"{old_label}/{rel_path}",
        tofile=f"{new_label}/{rel_path}",
        lineterm=""
    ))

    if not diff:
        return None

    return "\n".join(line.rstrip("\n") for line in diff) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Diff two X4 source versions")
    parser.add_argument("v1", help="Old version (e.g. 8.00H4)")
    parser.add_argument("v2", help="New version (e.g. 9.00B1)")
    parser.add_argument("--all-languages", "-A", action="store_true",
                        help="Include all localization languages (default: English only)")
    parser.add_argument("--only", metavar="DIR",
                        help="Only diff files under this subdirectory (e.g. libraries)")
    args = parser.parse_args()

    dir_old = SOURCE_DIR / args.v1
    dir_new = SOURCE_DIR / args.v2

    if not dir_old.is_dir():
        sys.exit(f"Source directory not found: {dir_old}")
    if not dir_new.is_dir():
        sys.exit(f"Source directory not found: {dir_new}")

    out_dir = DIFF_DIR / f"{args.v1}-{args.v2}"
    out_dir.mkdir(parents=True, exist_ok=True)

    files_old = collect_files(dir_old)
    files_new = collect_files(dir_new)

    def is_localization(rel: str) -> bool:
        """Check if path is inside a t/ localization directory."""
        parts = Path(rel).parts
        return "t" in parts and parts[-1].startswith("0001-l")

    def is_english_localization(rel: str) -> bool:
        return is_localization(rel) and "l044" in Path(rel).name

    def include(rel: str) -> bool:
        # Skip non-text files
        if Path(rel).suffix.lower() not in TEXT_EXTENSIONS:
            return False
        # Skip non-English localization unless --all-languages
        if not args.all_languages and is_localization(rel) and not is_english_localization(rel):
            return False
        if args.only and not rel.startswith(args.only.rstrip("/") + "/") and rel != args.only:
            return False
        return True

    files_old = {f for f in files_old if include(f)}
    files_new = {f for f in files_new if include(f)}

    added = sorted(files_new - files_old)
    removed = sorted(files_old - files_new)
    common = sorted(files_old & files_new)

    stats = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}

    all_files = [(rel, "modified") for rel in common]
    all_files += [(rel, "added") for rel in added]
    all_files += [(rel, "removed") for rel in removed]

    for rel, change_type in all_files:
        old = dir_old / rel if change_type != "added" else Path(os.devnull)
        new = dir_new / rel if change_type != "removed" else Path(os.devnull)
        diff_text = make_diff(old, new, rel, args.v1, args.v2)

        if diff_text is None:
            stats["unchanged"] += 1
            continue

        stats[change_type] += 1
        diff_path = out_dir / (rel + ".diff")
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text(diff_text, encoding="utf-8")

    total = stats["added"] + stats["removed"] + stats["modified"]
    print(f"Added: {stats['added']}  Removed: {stats['removed']}  "
          f"Modified: {stats['modified']}  Unchanged: {stats['unchanged']}")
    print(f"{total} diff files → {out_dir}/")


if __name__ == "__main__":
    main()
