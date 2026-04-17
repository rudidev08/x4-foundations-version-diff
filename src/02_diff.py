#!/usr/bin/env python3
"""
Pipeline step 02 — produce a per-file artifact for every changed file.

Usage:
    python3 src/02_diff.py --v1 DIR --v2 DIR --out DIR

Input:
    <--out>/01_enumerate/enumeration.jsonl

Output:
    <--out>/02_diff/diffs/<path>.diff     (modified — unified diff with header)
    <--out>/02_diff/diffs/<path>.added    (added    — raw V2 file copy, no header)
    <--out>/02_diff/diffs/<path>.deleted  (deleted  — raw V1 file copy, no header)

Resumability:
    Skip any path whose artifact (any of the three extensions) already exists.
"""
import argparse
import difflib
import json
from pathlib import Path

from _lib import Progress, atomic_write_bytes, atomic_write_text


def artifact_path(diffs_dir: Path, rel: str, ext: str) -> Path:
    """<diffs_dir>/<rel><ext>  e.g. diffs/libraries/wares.xml.diff"""
    return diffs_dir / f"{rel}{ext}"


def build_modified_diff(v1_path: Path, v2_path: Path, rel: str,
                        v1_bytes: int, v2_bytes: int) -> str:
    v1_lines = v1_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    v2_lines = v2_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    body = "".join(difflib.unified_diff(v1_lines, v2_lines, fromfile=rel, tofile=rel, n=3))
    header = (
        f"# Source: {rel}\n"
        f"# Status: modified\n"
        f"# V1 bytes: {v1_bytes} | V2 bytes: {v2_bytes}\n"
        f"# ─────────────────────────────────────\n"
    )
    return header + body


def main():
    p = argparse.ArgumentParser(description="Diff changed files into per-file artifacts.")
    p.add_argument("--v1", required=True, type=Path)
    p.add_argument("--v2", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()

    enum_file = args.out / "01_enumerate" / "enumeration.jsonl"
    diffs_dir = args.out / "02_diff" / "diffs"

    entries = [json.loads(raw) for raw in enum_file.read_text().splitlines() if raw.strip()]
    progress = Progress("02_diff", len(entries))

    written = skipped = 0
    for entry in entries:
        rel = entry["path"]
        status = entry["status"]
        candidates = [artifact_path(diffs_dir, rel, ext) for ext in (".diff", ".added", ".deleted")]
        if any(c.exists() for c in candidates):
            progress.tick(f"skip {rel}")
            skipped += 1
            continue

        progress.tick(f"{rel} ({status})")
        if status == "modified":
            text = build_modified_diff(
                args.v1 / rel, args.v2 / rel, rel,
                entry["v1_bytes"], entry["v2_bytes"],
            )
            atomic_write_text(artifact_path(diffs_dir, rel, ".diff"), text)
        elif status == "added":
            atomic_write_bytes(artifact_path(diffs_dir, rel, ".added"),
                               (args.v2 / rel).read_bytes())
        elif status == "deleted":
            atomic_write_bytes(artifact_path(diffs_dir, rel, ".deleted"),
                               (args.v1 / rel).read_bytes())
        else:
            raise ValueError(f"unknown status {status!r} for {rel}")
        written += 1

    progress.log(f"wrote {written}, skipped {skipped}")


if __name__ == "__main__":
    main()
