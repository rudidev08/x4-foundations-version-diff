#!/usr/bin/env python3
"""
Scan X4 source trees and regenerate src/x4_schema_map.generated.json.

Usage:
    python3 src/_scan_schema.py --source DIR [--source DIR ...] --out PATH

For each source root, walks every XML file under the X4-specific scan roots
declared in x4_rules_schema_scan.py.

For each XML file, parses with xml.parsers.expat to collect:
  - the root element tag,
  - each distinct direct-child tag and its count,
  - whether every occurrence of each child carries a consistent id-bearing
    attribute (using the candidate order from x4_rules_schema_scan.py).

A file qualifies when at least one direct-child tag occurs >= 2 times with a
consistent id-bearing attribute across every occurrence. When multiple tags
qualify, the one with the highest occurrence count is chosen (ties broken
alphabetically). Files whose root tag is <diff> are DLC patches handled
specially by 03_chunk via `sel` selectors, not repeating children — they are
skipped.

Files that the pipeline's filter list excludes (material_library*, sound_library*,
non-XML assets, shadergl/, etc.) are skipped: the same X4 file-filter rule the
rest of the pipeline uses.

The output JSON shape is documented in spec.md ("Schema map" subsection under
03_chunk). Entries are sorted by peak_bytes_observed desc, then file asc.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.parsers import expat

from _lib import atomic_write_text
from x4_rules_file_filter import should_include
from x4_rules_schema_scan import (
    ID_ATTRIBUTE_CANDIDATES,
    choose_repeating_child_entity,
    iter_scan_roots,
)


def _iter_xml_files(source: Path):
    """Yield (file_path, rel_key) for every .xml file in the scan scope.

    Applies the pipeline's `should_include` filter so excluded basenames
    (material_library*, sound_library*, etc.) and excluded dirs never reach
    the map. Keeping one source of truth for "is this file gameplay-relevant."
    """
    for base_dir, rel_prefix in iter_scan_roots(source):
        for path in base_dir.rglob("*.xml"):
            if not path.is_file():
                continue
            rel = path.relative_to(base_dir).as_posix()
            rel_key = f"{rel_prefix}/{rel}"
            if not should_include(rel_key):
                continue
            yield path, rel_key


class _RootChildSummary:
    """Collects root tag + per-direct-child stats from a single XML file.

    We only care about depth 0 (the root start) and depth 1 (direct children).
    Grandchildren are ignored — they don't contribute to the decision.
    """

    __slots__ = ("root_tag", "depth", "child_counts", "child_attr_presence")

    def __init__(self):
        self.root_tag: Optional[str] = None
        self.depth = 0
        # tag -> count of occurrences as a direct child of root
        self.child_counts: dict[str, int] = {}
        # tag -> {attr -> count of direct-child occurrences carrying `attr`}
        self.child_attr_presence: dict[str, dict[str, int]] = {}

    def on_start(self, name: str, attrs: dict):
        if self.depth == 0:
            self.root_tag = name
        elif self.depth == 1:
            self.child_counts[name] = self.child_counts.get(name, 0) + 1
            slot = self.child_attr_presence.setdefault(name, {})
            for cand in ID_ATTRIBUTE_CANDIDATES:
                if cand in attrs:
                    slot[cand] = slot.get(cand, 0) + 1
        self.depth += 1

    def on_end(self, _name: str):
        self.depth -= 1


def _parse_file(path: Path) -> Optional[_RootChildSummary]:
    """Parse `path` with expat. Returns a summary, or None on parse failure.

    Returning None lets the caller treat malformed XML as "not splittable"
    without bringing down the whole scan — the bound on data loss is one
    file per parse failure, and the count is reported to stderr.
    """
    summary = _RootChildSummary()
    parser = expat.ParserCreate()
    parser.StartElementHandler = summary.on_start
    parser.EndElementHandler = summary.on_end
    try:
        with path.open("rb") as fh:
            parser.ParseFile(fh)
    except expat.ExpatError:
        return None
    return summary


def _qualify(summary: _RootChildSummary) -> Optional[tuple[str, str]]:
    """Pick the best splittable direct child, or None if nothing qualifies."""
    return choose_repeating_child_entity(
        summary.root_tag,
        summary.child_counts,
        summary.child_attr_presence,
    )


def scan_sources(sources: list[Path]) -> tuple[list[dict], int]:
    """Scan every source in order. Returns (entries, parse_failure_count)."""
    best: dict[str, tuple[str, str]] = {}
    parse_failures = 0

    for source in sources:
        for path, rel_key in _iter_xml_files(source):
            summary = _parse_file(path)
            if summary is None:
                parse_failures += 1
                continue
            qualified = _qualify(summary)
            if qualified is None:
                continue
            # Same file across versions: the decision should match. First
            # occurrence wins; a divergent second occurrence is silently ignored.
            best.setdefault(rel_key, qualified)

    entries = [
        {"file": rel, "entity_tag": tag, "id_attribute": attr}
        for rel, (tag, attr) in sorted(best.items())
    ]
    return entries, parse_failures


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Scan X4 source trees and write src/x4_schema_map.generated.json."
    )
    ap.add_argument(
        "--source",
        action="append",
        required=True,
        type=Path,
        help="Source root to scan. Repeat for multiple roots.",
    )
    ap.add_argument("--out", required=True, type=Path, help="Path to write the schema map.")
    args = ap.parse_args(argv)

    missing = [str(s) for s in args.source if not s.is_dir()]
    if missing:
        joined = ", ".join(missing)
        print(
            f"[_scan_schema] ABORT: source dir(s) not found: {joined}. "
            f"Not touching {args.out}.",
            file=sys.stderr,
        )
        return 2

    entries, parse_failures = scan_sources(args.source)

    payload = {
        "last_scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scanned_sources": [str(s) for s in args.source],
        "entries": entries,
    }
    atomic_write_text(args.out, json.dumps(payload, indent=2) + "\n")

    print(
        f"[_scan_schema] wrote {len(entries)} entries to {args.out}"
        + (f" ({parse_failures} parse failure(s) skipped)" if parse_failures else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
