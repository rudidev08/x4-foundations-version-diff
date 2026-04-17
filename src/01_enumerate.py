#!/usr/bin/env python3
"""
Pipeline step 01 — walk V1 and V2 source trees, emit the changed-files manifest.

Usage:
    python3 src/01_enumerate.py --v1 DIR --v2 DIR --out DIR

Output:
    <--out>/01_enumerate/enumeration.jsonl
    One JSON line per changed file: {path, status, v1_bytes, v2_bytes}.
    status ∈ {"added", "modified", "deleted"}.

Resumability:
    If enumeration.jsonl already exists, the step is a no-op. Delete the
    file to force a re-scan.
"""
import argparse
import json
from pathlib import Path

from _lib import Progress, atomic_write_text
from x4_rules_file_filter import walk_filtered


def files_differ(left: Path, right: Path, chunk_size: int = 1024 * 1024) -> bool:
    """Return True when two files differ byte-for-byte."""
    with left.open("rb") as left_file, right.open("rb") as right_file:
        while True:
            left_chunk = left_file.read(chunk_size)
            right_chunk = right_file.read(chunk_size)
            if left_chunk != right_chunk:
                return True
            if not left_chunk:
                return False


def enumerate_changes(v1: Path, v2: Path):
    v2_files = set(walk_filtered(v2))
    v1_files = set(walk_filtered(v1))

    for rel in sorted(v2_files):
        v2_path = v2 / rel
        v2_bytes = v2_path.stat().st_size
        if rel in v1_files:
            v1_path = v1 / rel
            v1_bytes = v1_path.stat().st_size
            if v1_bytes != v2_bytes or files_differ(v1_path, v2_path):
                yield {"path": rel, "status": "modified",
                       "v1_bytes": v1_bytes, "v2_bytes": v2_bytes}
        else:
            yield {"path": rel, "status": "added", "v1_bytes": 0, "v2_bytes": v2_bytes}

    for rel in sorted(v1_files - v2_files):
        v1_bytes = (v1 / rel).stat().st_size
        yield {"path": rel, "status": "deleted", "v1_bytes": v1_bytes, "v2_bytes": 0}


def main():
    p = argparse.ArgumentParser(description="Enumerate changed files between V1 and V2.")
    p.add_argument("--v1", required=True, type=Path)
    p.add_argument("--v2", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()

    out_path = args.out / "01_enumerate" / "enumeration.jsonl"
    progress = Progress("01_enumerate", 1)
    if out_path.exists():
        progress.log(f"skip — {out_path.relative_to(args.out)} already exists")
        return

    progress.log(f"scanning {args.v1} → {args.v2}")
    lines = [json.dumps(e) for e in enumerate_changes(args.v1, args.v2)]
    atomic_write_text(out_path, ("\n".join(lines) + "\n") if lines else "")
    progress.log(f"{len(lines)} changed files → {out_path.relative_to(args.out)}")


if __name__ == "__main__":
    main()
