#!/usr/bin/env python3
"""
Pipeline step 03 — pack per-file diffs/files into LLM-ready chunks ≤ CHUNK_KB.

Usage:
    python3 src/03_chunk.py --v1 DIR --v2 DIR --out DIR --chunk-kb N
        [--force-split] [--schema-map PATH]

Input:
    <--out>/02_diff/diffs/*.{diff,added,deleted}
    <--v1>, <--v2>: original source trees (needed by level-1 splitter).

Output:
    <--out>/03_chunk/chunks/<chunk_id>.txt
    Each chunk has a plain-text header (Chunk, Entities, optional Sub-part)
    followed by the packed body. Byte-sized chunks can still be split when
    their content is too dense for weaker local LLMs. See spec.md →
    "Chunk header format".

Splitter levels (from spec.md):
    L1 schema-hinted split   — structural cut at repeating id-bearing elements.
    L2 generic XML split     — cut at depth boundaries in the diff/raw content.
    L3 Lua split             — cut at blank lines and function definitions.
    L4 force-split           — last-resort line-boundary cuts; --force-split only.
    L5 hard fail             — emits a descriptive SystemExit with options.
"""
from __future__ import annotations

import argparse
import json
import re
import xml.parsers.expat
from pathlib import Path
from typing import Callable

from _lib import (
    ALLOWED_PREFIXES_LINE_PREFIX,
    CHUNK_HEADER_SEPARATOR_PREFIX,
    DEFAULT_SCHEMA_MAP_FILENAME,
    Progress,
    atomic_write_text,
    count_changed_lines,
    count_hunks,
    file_fallback_prefix,
    load_schema_map,
)
from x4_rules_dlc_diff import build_dlc_diff_intervals, is_dlc_diff_file
from x4_rules_file_filter import normalize_source_path, strip_dlc_prefix
from x4_rules_chunk_profiles import (
    ChunkProfile,
    chunk_profile_for_source_path,
    complexity_score,
)
from x4_rules_file_entity_override import file_entity_override
from x4_rules_macro_parse import parse_singleton_macro
from x4_rules_macro_registry import resolve_macro_prefix


_DIFF_EXTS = (".diff", ".added", ".deleted")
_PREAMBLE_KEY_SUFFIX = "__preamble__"


# --------------------------------------------------------------------------- #
# Path / id helpers
# --------------------------------------------------------------------------- #

def source_path_from_diff(rel_diff_path: str) -> str:
    """Strip the .diff/.added/.deleted suffix to recover the original source path."""
    for ext in _DIFF_EXTS:
        if rel_diff_path.endswith(ext):
            return rel_diff_path[: -len(ext)]
    return rel_diff_path


def chunk_id(rel_diff_path: str, part: int, total: int) -> str:
    """Deterministic chunk id: libraries/wares.xml.diff p3/7 → libraries__wares.xml__part3of7."""
    stem = source_path_from_diff(rel_diff_path)
    return f"{stem.replace('/', '__')}__part{part}of{total}"


def _generic_semantic_entities(source_path: str, kind: str) -> list[str]:
    """Stable path-derived entity labels for generic splitters with no schema."""
    if kind != "xml":
        return []
    normalized = normalize_source_path(source_path)
    if normalized.startswith("aiscripts/") and normalized.endswith(".xml"):
        stem = Path(normalized).stem
        if stem:
            return [f"aiscript:{stem}"]
    return []


def _canonical_allowed_prefix(label: str) -> str | None:
    if not label or label == "entire file" or label.startswith("lines:"):
        return None
    if ":lines:" in label:
        label = label.split(":lines:", 1)[0]
    if label.endswith(f":{_PREAMBLE_KEY_SUFFIX}") or label.endswith(":__top__"):
        return None
    return label


def normalize_allowed_prefixes(
    source_path: str,
    entities: list[str],
    allowed_prefixes: list[str] | None = None,
) -> list[str]:
    raw = allowed_prefixes if allowed_prefixes is not None else entities
    result: list[str] = []
    seen: set[str] = set()
    for label in raw:
        prefix = _canonical_allowed_prefix(label)
        if prefix and prefix not in seen:
            result.append(prefix)
            seen.add(prefix)
    fallback = file_fallback_prefix(source_path)
    if fallback not in seen:
        result.append(fallback)
    return result


def _entity_weight(label: str) -> int:
    prefix = _canonical_allowed_prefix(label)
    if prefix is None or prefix.startswith("file:"):
        return 0
    return 1


def count_entities(labels: list[str]) -> int:
    return sum(_entity_weight(label) for label in labels)


def chunk_complexity_score(
    body: str,
    entity_count: int,
    profile: ChunkProfile,
    *,
    subpart_count: int = 1,
) -> int:
    return complexity_score(
        profile=profile,
        entity_count=entity_count,
        changed_line_count=count_changed_lines(body),
        hunk_count=count_hunks(body),
        subpart_count=subpart_count,
    )


def chunk_is_too_complex(
    body: str,
    entity_count: int,
    profile: ChunkProfile,
    *,
    subpart_count: int = 1,
) -> bool:
    changed_lines = count_changed_lines(body)
    hunks = count_hunks(body)
    return (
        entity_count > profile.max_entities_per_chunk
        or len(body.encode("utf-8")) > profile.max_body_bytes_per_chunk
        or changed_lines > profile.max_changed_lines_per_chunk
        or hunks > profile.max_hunks_per_chunk
        or complexity_score(
            profile=profile,
            entity_count=entity_count,
            changed_line_count=changed_lines,
            hunk_count=hunks,
            subpart_count=subpart_count,
        ) > profile.max_complexity_score
    )


def chunk_header(
    source_path: str,
    part: int,
    total: int,
    entities: list[str],
    sub_part: str | None = None,
    allowed_prefixes: list[str] | None = None,
) -> str:
    if not entities:
        ent_line = "# Entities: entire file"
    elif len(entities) > 10:
        shown = ", ".join(entities[:10])
        ent_line = f"# Entities ({len(entities)}): {shown}, +{len(entities) - 10} more"
    else:
        ent_line = f"# Entities ({len(entities)}): {', '.join(entities)}"
    allowed = normalize_allowed_prefixes(source_path, entities, allowed_prefixes)
    lines = [f"# Chunk: {source_path} part {part}/{total}"]
    if sub_part:
        lines.append(f"# Sub-part: {sub_part}")
    lines.append(ent_line)
    lines.append(f"{ALLOWED_PREFIXES_LINE_PREFIX} {json.dumps(allowed)}")
    lines.append(f"{CHUNK_HEADER_SEPARATOR_PREFIX}────────────────────────────────────\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Expat entity-interval extraction
# --------------------------------------------------------------------------- #

def build_entity_intervals(
    xml_text: str,
    entity_tag: str,
    id_attribute: str,
) -> list[tuple[int, int, str]]:
    """Return [(start_line, end_line, "entity_tag:<id_value>")] for every top-matching element.

    Lines are 1-based and inclusive. If the same tag nests inside another instance
    (unusual but possible), only the outermost occurrence is recorded.
    """
    parser = xml.parsers.expat.ParserCreate()
    results: list[tuple[int, int, str]] = []
    stack: list[tuple[str, int]] = []  # (key, start_line)
    depth = 0  # depth of currently-open target elements

    def on_start(name: str, attrs: dict):
        nonlocal depth
        if name == entity_tag and depth == 0:
            id_value = attrs.get(id_attribute, "")
            key = f"{entity_tag}:{id_value}"
            stack.append((key, parser.CurrentLineNumber))
            depth += 1
        elif name == entity_tag:
            depth += 1

    def on_end(name: str):
        nonlocal depth
        if name == entity_tag:
            depth -= 1
            if depth == 0 and stack:
                key, start = stack.pop()
                results.append((start, parser.CurrentLineNumber, key))

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(xml_text, True)
    except xml.parsers.expat.ExpatError:
        # Malformed XML — fall back to whatever we collected before the error.
        pass
    results.sort(key=lambda iv: iv[0])
    return results


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _entity_keys_from_intervals(intervals: list[tuple[int, int, str]]) -> list[str]:
    return _dedupe_preserve_order([key for _, _, key in intervals if key])


def build_child_intervals(
    xml_text: str,
    parent_start_line: int,
    parent_end_line: int,
) -> list[tuple[int, int, str]]:
    """Find the direct child elements of a parent element identified by line range.

    Returns [(start_line, end_line, "child_tag#N")] for each direct child, with
    an ordinal suffix so repeats of the same tag get distinct keys (necessary
    for per-child packing). Used for recursive splitting when a single entity
    exceeds CHUNK_KB.
    """
    parser = xml.parsers.expat.ParserCreate()
    results: list[tuple[int, int, str]] = []
    stack: list[tuple[str, int]] = []
    depth = 0
    inside_parent = False
    tag_counts: dict[str, int] = {}

    def on_start(name: str, attrs: dict):
        nonlocal depth, inside_parent
        line = parser.CurrentLineNumber
        if not inside_parent and line >= parent_start_line and line <= parent_end_line:
            inside_parent = True
            depth = 0
            return
        if inside_parent:
            if depth == 0:
                stack.append((name, line))
            depth += 1

    def on_end(name: str):
        nonlocal depth, inside_parent
        line = parser.CurrentLineNumber
        if inside_parent:
            if depth == 0:
                # Leaving parent.
                inside_parent = False
                return
            depth -= 1
            if depth == 0 and stack:
                tag, start = stack.pop()
                ordinal = tag_counts.get(tag, 0)
                tag_counts[tag] = ordinal + 1
                results.append((start, line, f"{tag}#{ordinal}"))

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(xml_text, True)
    except xml.parsers.expat.ExpatError:
        pass
    results.sort(key=lambda iv: iv[0])
    return results


def _child_tag_from_key(key: str) -> str:
    """entry#0 -> entry"""
    return key.split("#", 1)[0] if "#" in key else key


# --------------------------------------------------------------------------- #
# Unified-diff parsing
# --------------------------------------------------------------------------- #

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def split_unified_diff(body: str) -> tuple[str, str, list[str], list[tuple[int, int, str]]]:
    """Split a diff artifact into preserved-header, file-lines (---/+++), and hunks.

    Returns (header, file_lines, diff_lines, hunks) where:
      * header:    everything before the first `--- ` line (includes `# Source: ...` metadata).
      * file_lines: the two lines `--- a\n+++ b\n` (or empty if not present).
      * diff_lines: list of non-hunk context that we re-emit before the hunks (currently empty).
      * hunks:     [(v2_core_start_line, v2_core_end_line, hunk_text_with_header)]
                   where core = first-to-last `+`/`-` line. Falls back to the
                   full `+c,d` range when the hunk has no `+`/`-` lines (rare).
    """
    lines = body.splitlines(keepends=True)
    i = 0
    header_lines: list[str] = []
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") or line.startswith("@@ "):
            break
        header_lines.append(line)
        i += 1
    file_lines: list[str] = []
    if i < len(lines) and lines[i].startswith("--- "):
        file_lines.append(lines[i]); i += 1
        if i < len(lines) and lines[i].startswith("+++ "):
            file_lines.append(lines[i]); i += 1
    hunks: list[tuple[int, int, str]] = []
    while i < len(lines):
        line = lines[i]
        m = _HUNK_RE.match(line.rstrip("\n"))
        if not m:
            i += 1
            continue
        c = int(m.group(3))
        d = int(m.group(4) or 1)
        full_start = c
        full_end = c + d - 1 if d > 0 else c
        hunk_start = i
        i += 1
        # Walk the hunk body, tracking the V2 line number per row.
        # '+' and ' ' advance V2; '-' does not. We want the core range:
        # the smallest inclusive V2 span that covers every '+' or '-' line.
        core_lo: int | None = None
        core_hi: int | None = None
        cursor = c  # V2 line number of the next ' ' / '+' row
        while i < len(lines) and not lines[i].startswith("@@ "):
            row = lines[i]
            if row.startswith("+"):
                core_lo = cursor if core_lo is None else core_lo
                core_hi = cursor
                cursor += 1
            elif row.startswith("-"):
                # Deletion: anchor at current cursor (points to the V2 line
                # the deletion sits between).
                anchor = cursor
                core_lo = anchor if core_lo is None else core_lo
                core_hi = max(core_hi, anchor) if core_hi is not None else anchor
            elif row.startswith(" "):
                cursor += 1
            # '\ No newline at end of file' and similar markers are ignored.
            i += 1
        hunk_text = "".join(lines[hunk_start:i])
        if core_lo is None or core_hi is None:
            core_lo, core_hi = full_start, full_end
        hunks.append((core_lo, core_hi, hunk_text))
    return "".join(header_lines), "".join(file_lines), [], hunks


def split_hunk_at_boundaries(
    hunk_text: str,
    boundaries: list[int],
) -> list[tuple[int, int, str]]:
    """Break a single hunk into one or more sub-hunks, cutting at the given
    V2 line boundaries (1-based, inclusive list of line numbers on which a new
    sub-hunk should start).

    Returns [(v2_core_start, v2_core_end, hunk_text_with_synth_header)].

    Each sub-hunk is reassembled as a fresh `@@ -a,b +c,d @@` block using only
    the rows that belong in it. Rows are assigned by V2 cursor (+ and space
    rows advance V2; - rows anchor to the current V2 cursor). A space-only
    sub-hunk is dropped — there's nothing meaningful to carry.
    """
    lines = hunk_text.splitlines(keepends=True)
    if not lines:
        return []
    m = _HUNK_RE.match(lines[0].rstrip("\n"))
    if not m:
        return []
    v1_start = int(m.group(1))
    v2_start = int(m.group(3))
    # Walk rows, recording (v1_cursor, v2_cursor, row).
    rows: list[tuple[int, int, str]] = []
    v1 = v1_start
    v2 = v2_start
    for row in lines[1:]:
        if row.startswith("+"):
            rows.append((v1, v2, row))
            v2 += 1
        elif row.startswith("-"):
            rows.append((v1, v2, row))
            v1 += 1
        elif row.startswith(" "):
            rows.append((v1, v2, row))
            v1 += 1
            v2 += 1
        elif row.startswith("\\"):
            # '\ No newline at end of file' — pin to previous row's cursors.
            rows.append((v1, v2, row))

    if not rows:
        return []

    # Partition rows by which boundary bucket they fall into (by V2 cursor).
    sorted_bounds = sorted(set(boundaries))
    def bucket_for(v2_line: int) -> int:
        # Find the largest boundary ≤ v2_line.
        lo, hi = 0, len(sorted_bounds) - 1
        idx = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if sorted_bounds[mid] <= v2_line:
                idx = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return idx

    buckets: dict[int, list[tuple[int, int, str]]] = {}
    for r in rows:
        b = bucket_for(r[1])
        buckets.setdefault(b, []).append(r)

    out: list[tuple[int, int, str]] = []
    for b in sorted(buckets):
        brows = buckets[b]
        if not any(r[2].startswith(("+", "-")) for r in brows):
            continue  # context-only slice, skip
        v1_rows = [r for r in brows if r[2].startswith((" ", "-"))]
        v2_rows = [r for r in brows if r[2].startswith((" ", "+"))]
        hv1 = v1_rows[0][0] if v1_rows else brows[0][0]
        hv2 = v2_rows[0][1] if v2_rows else brows[0][1]
        body = "".join(r[2] for r in brows)
        new_header = f"@@ -{hv1},{len(v1_rows)} +{hv2},{len(v2_rows)} @@\n"
        # Core range = first to last + / - row in V2 coordinates.
        pm_rows = [r for r in brows if r[2].startswith(("+", "-"))]
        core_lo = pm_rows[0][1]
        core_hi = pm_rows[-1][1]
        out.append((core_lo, core_hi, new_header + body))
    return out


def reslice_hunks_at_interval_boundaries(
    hunks: list[tuple[int, int, str]],
    intervals: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    """Split hunks when they span multiple entity intervals."""
    if not intervals:
        return hunks

    interval_boundaries = sorted({iv[0] for iv in intervals} | {iv[1] + 1 for iv in intervals})
    resliced: list[tuple[int, int, str]] = []
    for hunk in hunks:
        hs, he, _ = hunk
        if any(istart <= hs and he <= iend for istart, iend, _key in intervals):
            resliced.append(hunk)
            continue
        overlaps = [iv for iv in intervals if iv[0] <= he and iv[1] >= hs]
        if not overlaps:
            resliced.append(hunk)
            continue
        sub = split_hunk_at_boundaries(hunk[2], interval_boundaries)
        resliced.extend(sub if sub else [hunk])
    return resliced


# --------------------------------------------------------------------------- #
# Hunk → interval assignment
# --------------------------------------------------------------------------- #

def _preamble_key(entity_tag: str) -> str:
    return f"{entity_tag}:{_PREAMBLE_KEY_SUFFIX}"


def _translate_entity_key(key: str, entity_label_overrides: dict[str, str] | None) -> str:
    if not entity_label_overrides:
        return key
    return entity_label_overrides.get(key, key)


def _translate_entity_keys(
    keys: list[str],
    entity_label_overrides: dict[str, str] | None,
) -> list[str]:
    translated: list[str] = []
    for key in keys:
        display = _translate_entity_key(key, entity_label_overrides)
        if _canonical_allowed_prefix(display) is None:
            continue
        translated.append(display)
    return _dedupe_preserve_order(translated)


def assign_hunks_to_entities(
    hunks: list[tuple[int, int, str]],
    intervals: list[tuple[int, int, str]],
    preamble_key: str,
) -> list[tuple[str, tuple[int, int, str]]]:
    """Map each hunk to the smallest-range interval that fully contains it.

    Hunks outside all intervals map to `preamble_key`. Returns an ordered list
    preserving hunk order: [(entity_key, hunk), ...].
    """
    out: list[tuple[str, tuple[int, int, str]]] = []
    for hunk in hunks:
        hs, he, _ = hunk
        best: tuple[int, int, str] | None = None
        best_range = None
        for istart, iend, key in intervals:
            if istart <= hs and he <= iend:
                span = iend - istart
                if best_range is None or span < best_range:
                    best = (istart, iend, key)
                    best_range = span
        if best is None:
            out.append((preamble_key, hunk))
        else:
            out.append((best[2], hunk))
    return out


def group_hunks_by_entity(
    assignments: list[tuple[str, tuple[int, int, str]]],
) -> list[tuple[str, list[tuple[int, int, str]]]]:
    """Preserve first-appearance order; collect all hunks per entity."""
    order: list[str] = []
    buckets: dict[str, list[tuple[int, int, str]]] = {}
    for key, hunk in assignments:
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(hunk)
    return [(k, buckets[k]) for k in order]


def touched_entity_keys_for_diff(
    body: str,
    intervals: list[tuple[int, int, str]],
    preamble_key: str,
) -> list[str]:
    """Return the ordered entity keys touched by a diff body.

    Preamble-only assignments stay file-scoped and are intentionally excluded.
    """
    _, _, _, hunks = split_unified_diff(body)
    if not hunks or not intervals:
        return []
    hunks = reslice_hunks_at_interval_boundaries(hunks, intervals)
    assignments = assign_hunks_to_entities(hunks, intervals, preamble_key)
    grouped = group_hunks_by_entity(assignments)
    return [key for key, _ in grouped if key != preamble_key]


def _smallest_interval_key_for_line(
    line_no: int,
    intervals: list[tuple[int, int, str]],
    preamble_key: str,
) -> str:
    best_key = preamble_key
    best_range: int | None = None
    for start, end, key in intervals:
        if start <= line_no <= end:
            span = end - start
            if best_range is None or span < best_range:
                best_key = key
                best_range = span
    return best_key


def _entity_key_for_diff_row(
    row: str,
    v1_line: int,
    v2_line: int,
    v1_intervals: list[tuple[int, int, str]],
    v2_intervals: list[tuple[int, int, str]],
    preamble_key: str,
) -> str:
    if row.startswith("+"):
        return _smallest_interval_key_for_line(v2_line, v2_intervals, preamble_key)
    if row.startswith("-"):
        return _smallest_interval_key_for_line(v1_line, v1_intervals, preamble_key)
    if row.startswith(" "):
        v2_key = _smallest_interval_key_for_line(v2_line, v2_intervals, preamble_key)
        v1_key = _smallest_interval_key_for_line(v1_line, v1_intervals, preamble_key)
        if v2_key == v1_key:
            return v2_key
        if v2_key != preamble_key:
            return v2_key
        if v1_key != preamble_key:
            return v1_key
    return preamble_key


def _build_subhunk_from_rows(
    rows: list[tuple[int, int, str]],
) -> tuple[int, int, str] | None:
    if not rows or not any(row.startswith(("+", "-")) for _, _, row in rows):
        return None

    v1_rows = [row for row in rows if row[2].startswith((" ", "-"))]
    v2_rows = [row for row in rows if row[2].startswith((" ", "+"))]
    hv1 = v1_rows[0][0] if v1_rows else rows[0][0]
    hv2 = v2_rows[0][1] if v2_rows else rows[0][1]
    body = "".join(row for _, _, row in rows)
    new_header = f"@@ -{hv1},{len(v1_rows)} +{hv2},{len(v2_rows)} @@\n"

    start = hv2 if v2_rows else hv1
    end = (v2_rows[-1][1] if v2_rows else v1_rows[-1][0]) if (v2_rows or v1_rows) else start
    return start, end, new_header + body


def split_modified_hunk_by_entities(
    hunk_text: str,
    v1_intervals: list[tuple[int, int, str]],
    v2_intervals: list[tuple[int, int, str]],
    preamble_key: str,
) -> list[tuple[str, tuple[int, int, str]]]:
    """Split a modified XML hunk into entity-scoped sub-hunks across both sides."""
    lines = hunk_text.splitlines(keepends=True)
    if not lines:
        return []

    match = _HUNK_RE.match(lines[0].rstrip("\n"))
    if not match:
        return [(preamble_key, (0, 0, hunk_text))]

    v1 = int(match.group(1))
    v2 = int(match.group(3))
    current_key: str | None = None
    current_rows: list[tuple[int, int, str]] = []
    assignments: list[tuple[str, tuple[int, int, str]]] = []
    last_key = preamble_key

    def flush() -> None:
        nonlocal current_rows
        if current_key is None:
            current_rows = []
            return
        built = _build_subhunk_from_rows(current_rows)
        if built is not None:
            assignments.append((current_key, built))
        current_rows = []

    for row in lines[1:]:
        if row.startswith("\\"):
            row_key = current_key or last_key
        else:
            row_key = _entity_key_for_diff_row(
                row,
                v1,
                v2,
                v1_intervals,
                v2_intervals,
                preamble_key,
            )

        if current_key is None:
            current_key = row_key
        elif row_key != current_key:
            flush()
            current_key = row_key
        current_rows.append((v1, v2, row))
        last_key = row_key

        if row.startswith("+"):
            v2 += 1
        elif row.startswith("-"):
            v1 += 1
        elif row.startswith(" "):
            v1 += 1
            v2 += 1

    flush()
    return assignments or [(preamble_key, (int(match.group(3)), int(match.group(3)), hunk_text))]


def touched_deleted_entity_keys_for_diff(
    body: str,
    intervals: list[tuple[int, int, str]],
    preamble_key: str,
) -> list[str]:
    """Return ordered entity keys touched by deleted-side rows in a diff body."""
    _, _, _, hunks = split_unified_diff(body)
    if not hunks or not intervals:
        return []

    touched: list[str] = []
    seen: set[str] = set()
    for _core_start, _core_end, hunk_text in hunks:
        lines = hunk_text.splitlines(keepends=True)
        if not lines:
            continue
        match = _HUNK_RE.match(lines[0].rstrip("\n"))
        if not match:
            continue
        v1_cursor = int(match.group(1))
        for row in lines[1:]:
            if row.startswith("-"):
                key = _smallest_interval_key_for_line(v1_cursor, intervals, preamble_key)
                if key != preamble_key and key not in seen:
                    touched.append(key)
                    seen.add(key)
                v1_cursor += 1
            elif row.startswith(" "):
                v1_cursor += 1
            elif row.startswith("+"):
                continue
    return touched


# --------------------------------------------------------------------------- #
# Mini-diff / packer
# --------------------------------------------------------------------------- #

def build_mini_diff(header: str, file_lines: str, hunks: list[tuple[int, int, str]]) -> str:
    """Reassemble a partial diff body: preserved header + `--- `/`+++ ` + selected hunks."""
    return header + file_lines + "".join(h[2] for h in hunks)


class OversizeGroupError(Exception):
    """A single pack group exceeds the per-chunk limits on its own.

    `is_byte_overflow` separates the hard LLM-context case (`size > max_bytes`,
    the chunk literally can't fit the model) from soft overflows:
    `max_body_soft_bytes` (profile per-chunk budget) and the complexity caps
    (changed lines / entities / hunks / score). Hard overflow bubbles up to
    fail the pipeline or trigger force-split. Soft overflow is emitted as
    its own chunk with a '# WARNING:' header and the LLM still processes it.
    """

    def __init__(
        self,
        key: str,
        *,
        size: int,
        max_bytes: int,
        max_body_soft_bytes: int | None = None,
        changed_lines: int,
        max_changed_lines: int,
        entities: int,
        max_entities: int,
        hunks: int,
        max_hunks: int,
        score: int,
        max_score: int,
    ):
        self.key = key
        self.size = size
        self.max_bytes = max_bytes
        self.max_body_soft_bytes = (
            max_body_soft_bytes if max_body_soft_bytes is not None else max_bytes
        )
        self.changed_lines = changed_lines
        self.max_changed_lines = max_changed_lines
        self.entities = entities
        self.max_entities = max_entities
        self.hunks = hunks
        self.max_hunks = max_hunks
        self.score = score
        self.max_score = max_score
        reasons: list[str] = []
        if size > max_bytes:
            reasons.append(f"{size} B > LLM context {max_bytes} B")
        elif size > self.max_body_soft_bytes:
            reasons.append(
                f"body {size} B > weak-model soft budget {self.max_body_soft_bytes} B"
            )
        if changed_lines > max_changed_lines:
            reasons.append(f"{changed_lines} changed lines > {max_changed_lines}")
        if entities > max_entities:
            reasons.append(f"{entities} entities > {max_entities}")
        if hunks > max_hunks:
            reasons.append(f"{hunks} hunks > {max_hunks}")
        if score > max_score:
            reasons.append(f"complexity {score} > {max_score}")
        self.detail = ", ".join(reasons) if reasons else "unknown limit overflow"
        super().__init__(f"{key} exceeds single-chunk limits: {self.detail}")

    @property
    def is_byte_overflow(self) -> bool:
        return self.size > self.max_bytes


def _group_metrics(
    body: str,
    weight_key: str,
    profile: ChunkProfile,
    *,
    subpart_count: int = 1,
) -> tuple[int, int, int, int, int]:
    size = len(body.encode("utf-8"))
    changed_lines = count_changed_lines(body)
    entities = _entity_weight(weight_key)
    hunks = count_hunks(body)
    score = complexity_score(
        profile=profile,
        entity_count=entities,
        changed_line_count=changed_lines,
        hunk_count=hunks,
        subpart_count=subpart_count,
    )
    return size, changed_lines, entities, hunks, score


def _ensure_group_fits(
    group_label: str,
    body: str,
    max_bytes: int,
    profile: ChunkProfile,
    *,
    weight_key: str | None = None,
    subpart_count: int = 1,
) -> tuple[int, int, int, int, int]:
    """Compute group metrics. Raises OversizeGroupError if any cap is exceeded.

    `max_bytes` is the hard cap — the LLM's context window (CHUNK_KB * 1024).
    The profile's `max_body_bytes_per_chunk` is a soft cap that keeps chunks
    small for weaker models; it's factored into the effective cap used for
    the raise decision, but the stored `OversizeGroupError.max_bytes` is the
    hard one. That way `err.is_byte_overflow` correctly distinguishes
    "doesn't fit the model" (hard fail) from "exceeds soft quality budget"
    (emit with warning) in callers.

    Level-1 splitters use the raise as a signal to try recursive splitting on
    child boundaries. pack_groups and _pack_expanded_groups catch the raise
    and route hard-byte overflow up (fail or force-split) while emitting soft
    overflows as their own part with a '# WARNING:' header via
    _complexity_overflow_warning.
    """
    resolved_weight_key = group_label if weight_key is None else weight_key
    size, changed_lines, entities, hunks, score = _group_metrics(
        body,
        resolved_weight_key,
        profile,
        subpart_count=subpart_count,
    )
    effective_max_bytes = min(max_bytes, profile.max_body_bytes_per_chunk)
    if (
        size > effective_max_bytes
        or changed_lines > profile.max_changed_lines_per_chunk
        or entities > profile.max_entities_per_chunk
        or hunks > profile.max_hunks_per_chunk
        or score > profile.max_complexity_score
    ):
        raise OversizeGroupError(
            group_label,
            size=size,
            max_bytes=max_bytes,
            max_body_soft_bytes=profile.max_body_bytes_per_chunk,
            changed_lines=changed_lines,
            max_changed_lines=profile.max_changed_lines_per_chunk,
            entities=entities,
            max_entities=profile.max_entities_per_chunk,
            hunks=hunks,
            max_hunks=profile.max_hunks_per_chunk,
            score=score,
            max_score=profile.max_complexity_score,
        )
    return size, changed_lines, entities, hunks, score


def _complexity_overflow_warning(
    body: str,
    keys: list[str],
    profile: ChunkProfile,
    *,
    subpart_count: int = 1,
) -> str | None:
    """Return a warning line if this chunk exceeds any soft weak-model cap.

    Hard LLM-context overflow is checked elsewhere and raises. This function
    only fires when a chunk fits the LLM context but exceeds the profile's
    per-chunk soft byte budget or any complexity cap. The caller inserts the
    returned line into the chunk header so the LLM can see the chunk might
    be harder to process correctly.
    """
    size = len(body.encode("utf-8"))
    changed_lines = count_changed_lines(body)
    hunks = count_hunks(body)
    entities = sum(_entity_weight(k) for k in keys)
    score = complexity_score(
        profile=profile,
        entity_count=entities,
        changed_line_count=changed_lines,
        hunk_count=hunks,
        subpart_count=subpart_count,
    )
    reasons: list[str] = []
    if size > profile.max_body_bytes_per_chunk:
        reasons.append(
            f"body {size} B > weak-model soft budget {profile.max_body_bytes_per_chunk} B"
        )
    if changed_lines > profile.max_changed_lines_per_chunk:
        reasons.append(f"{changed_lines} changed lines > {profile.max_changed_lines_per_chunk}")
    if entities > profile.max_entities_per_chunk:
        reasons.append(f"{entities} entities > {profile.max_entities_per_chunk}")
    if hunks > profile.max_hunks_per_chunk:
        reasons.append(f"{hunks} hunks > {profile.max_hunks_per_chunk}")
    if score > profile.max_complexity_score:
        reasons.append(f"complexity {score} > {profile.max_complexity_score}")
    if not reasons:
        return None
    return (
        "# WARNING: chunk fits the LLM context but is too dense for weaker "
        f"models ({', '.join(reasons)})"
    )


def pack_groups(
    groups: list[tuple[str, str]],
    max_bytes: int,
    profile: ChunkProfile,
) -> list[list[tuple[str, str]]]:
    """Linear-fill pack: walk groups, add to current part while each fits.

    A group that alone exceeds complexity caps (but fits the byte budget) is
    flushed to its own part; the caller-side writer marks that part with a
    complexity warning header. Byte-cap overflow still propagates so callers
    can trigger force-split or hard-fail with an explicit message.
    """
    effective_max_bytes = min(max_bytes, profile.max_body_bytes_per_chunk)
    parts: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_size = 0
    current_changed_lines = 0
    current_entities = 0
    current_hunks = 0
    current_score = 0

    def flush_current() -> None:
        nonlocal current, current_size, current_changed_lines
        nonlocal current_entities, current_hunks, current_score
        if current:
            parts.append(current)
        current = []
        current_size = 0
        current_changed_lines = 0
        current_entities = 0
        current_hunks = 0
        current_score = 0

    for key, body in groups:
        try:
            size, changed_lines, entities, hunks, score = _ensure_group_fits(
                key,
                body,
                max_bytes,
                profile=profile,
            )
        except OversizeGroupError as err:
            if err.is_byte_overflow:
                raise
            # Soft overflow (profile byte cap or complexity): emit as its
            # own part; the writer adds a warning header on that chunk.
            flush_current()
            parts.append([(key, body)])
            continue
        if current and (
            current_size + size > effective_max_bytes
            or current_changed_lines + changed_lines > profile.max_changed_lines_per_chunk
            or current_entities + entities > profile.max_entities_per_chunk
            or current_hunks + hunks > profile.max_hunks_per_chunk
            or current_score + score > profile.max_complexity_score
        ):
            flush_current()
        current.append((key, body))
        current_size += size
        current_changed_lines += changed_lines
        current_entities += entities
        current_hunks += hunks
        current_score += score
    if current:
        parts.append(current)
    return parts


def _pack_expanded_groups(
    entries: list[tuple[str, str, str | None]],
    max_bytes: int,
    profile: ChunkProfile,
    *,
    weight_key_resolver: Callable[[str], str] | None = None,
) -> list[list[tuple[str, str, str | None]]]:
    """Pack pre-rendered groups, forcing labeled sub-parts to remain isolated.

    Mirrors pack_groups: a group that alone exceeds complexity caps (but fits
    the byte budget) becomes its own part with a writer-side warning header.
    Byte-cap overflow still raises.
    """
    effective_max_bytes = min(max_bytes, profile.max_body_bytes_per_chunk)
    parts: list[list[tuple[str, str, str | None]]] = []
    current: list[tuple[str, str, str | None]] = []
    current_size = 0
    current_changed_lines = 0
    current_entities = 0
    current_hunks = 0
    current_score = 0

    def flush_current() -> None:
        nonlocal current, current_size, current_changed_lines
        nonlocal current_entities, current_hunks, current_score
        if current:
            parts.append(current)
        current = []
        current_size = 0
        current_changed_lines = 0
        current_entities = 0
        current_hunks = 0
        current_score = 0

    for key, body, sub in entries:
        weight_key = weight_key_resolver(key) if weight_key_resolver is not None else key
        try:
            size, changed_lines, entities, hunks, score = _ensure_group_fits(
                weight_key,
                body,
                max_bytes,
                profile=profile,
                weight_key=weight_key,
                subpart_count=1 if sub is None else 2,
            )
        except OversizeGroupError as err:
            if err.is_byte_overflow:
                raise
            flush_current()
            parts.append([(key, body, sub)])
            continue
        if sub is not None:
            flush_current()
            parts.append([(key, body, sub)])
            continue
        if current and (
            current_size + size > effective_max_bytes
            or current_changed_lines + changed_lines > profile.max_changed_lines_per_chunk
            or current_entities + entities > profile.max_entities_per_chunk
            or current_hunks + hunks > profile.max_hunks_per_chunk
            or current_score + score > profile.max_complexity_score
        ):
            flush_current()
        current.append((key, body, sub))
        current_size += size
        current_changed_lines += changed_lines
        current_entities += entities
        current_hunks += hunks
        current_score += score

    if current:
        parts.append(current)
    return parts


# --------------------------------------------------------------------------- #
# Level-1 implementations
# --------------------------------------------------------------------------- #

def level1_modified(
    rel_diff: str,
    source_path: str,
    body: str,
    v1_source_text: str,
    v2_source_text: str,
    entity_tag: str,
    id_attribute: str,
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
    is_dlc: bool = False,
    entity_label_overrides: dict[str, str] | None = None,
) -> int:
    """Level-1 split for a .diff (modified) oversize artifact. Returns count written."""
    if is_dlc:
        intervals = build_dlc_diff_intervals(v2_source_text)
        preamble = _preamble_key("diff")
        v1_intervals: list[tuple[int, int, str]] = []
    else:
        intervals = build_entity_intervals(v2_source_text, entity_tag, id_attribute)
        preamble = _preamble_key(entity_tag)
        v1_intervals = (
            build_entity_intervals(v1_source_text, entity_tag, id_attribute)
            if v1_source_text else []
        )

    header, file_lines, _, hunks = split_unified_diff(body)

    if is_dlc:
        # DLC diffs only have V2-side intervals.
        hunks = reslice_hunks_at_interval_boundaries(hunks, intervals)
        assignments = assign_hunks_to_entities(hunks, intervals, preamble)
    else:
        assignments = []
        for _core_start, _core_end, hunk_text in hunks:
            assignments.extend(
                split_modified_hunk_by_entities(
                    hunk_text,
                    v1_intervals,
                    intervals,
                    preamble,
                )
            )
    groups = group_hunks_by_entity(assignments)

    # Build mini-diff per group.
    rendered: list[tuple[str, str, list[tuple[int, int, str]]]] = []
    for key, group_hunks in groups:
        mini = build_mini_diff(header, file_lines, group_hunks)
        rendered.append((key, mini, group_hunks))

    # First pass: identify groups whose own mini-diff exceeds max_bytes — those
    # need recursive splitting. Replace each with a sequence of sub-mini-diffs.
    expanded: list[tuple[str, str, str | None]] = []  # (key, mini, sub_label)
    effective_max_bytes = min(max_bytes, profile.max_body_bytes_per_chunk)
    for key, mini, group_hunks in rendered:
        needs_split = False
        try:
            _ensure_group_fits(
                _translate_entity_key(key, entity_label_overrides),
                mini,
                max_bytes,
                profile,
            )
        except OversizeGroupError:
            needs_split = True
        if needs_split and not is_dlc and key != preamble:
            interval = next(
                ((s, e) for s, e, k in intervals if k == key),
                None,
            )
            child_intervals = (
                build_child_intervals(v2_source_text, interval[0], interval[1])
                if interval is not None else []
            )
            if not child_intervals:
                expanded.append((key, mini, None))
                continue
            # Slice every hunk at child boundaries — this handles the case where
            # one hunk covers many child elements. Then bucket the resulting
            # sub-hunks by child interval and pack.
            boundaries = [ci[0] for ci in child_intervals]
            sliced: list[tuple[int, int, str]] = []
            for hunk in group_hunks:
                sliced.extend(split_hunk_at_boundaries(hunk[2], boundaries))
            if not sliced:
                # Fallback: keep original hunks unsliced.
                sliced = list(group_hunks)

            child_preamble = f"{key}:{_PREAMBLE_KEY_SUFFIX}"
            sub_assignments = assign_hunks_to_entities(
                sliced, child_intervals, child_preamble,
            )
            sub_groups = group_hunks_by_entity(sub_assignments)
            sub_by_key = dict(sub_groups)
            keyed_bodies = [
                (sub_key, build_mini_diff(header, file_lines, sub_hunks))
                for sub_key, sub_hunks in sub_groups
            ]
            sub_parts = pack_groups(keyed_bodies, max_bytes, profile)
            child_tag = _child_tag_from_key(child_intervals[0][2])
            total_sub = len(sub_parts)
            for idx, part_entries in enumerate(sub_parts, start=1):
                all_sub_hunks: list[tuple[int, int, str]] = []
                for sub_key, _body in part_entries:
                    all_sub_hunks.extend(sub_by_key.get(sub_key, []))
                all_sub_hunks.sort(key=lambda h: h[0])
                coherent = build_mini_diff(header, file_lines, all_sub_hunks)
                display_key = _translate_entity_key(key, entity_label_overrides)
                expanded.append((
                    key,
                    coherent,
                    f"{idx}/{total_sub} of {display_key} (split at <{child_tag}> boundaries)",
                ))
        else:
            expanded.append((key, mini, None))

    parts = _pack_expanded_groups(
        expanded,
        max_bytes,
        profile,
        weight_key_resolver=lambda key: _translate_entity_key(key, entity_label_overrides),
    )

    total = len(parts)
    written = 0
    for idx, part_entries in enumerate(parts, start=1):
        sub_label = next((s for _, _, s in part_entries if s is not None), None)
        body_out = "".join(mini for _, mini, _ in part_entries)
        deleted_keys = touched_deleted_entity_keys_for_diff(body_out, v1_intervals, preamble)
        keys = _translate_entity_keys(
            _dedupe_preserve_order([k for k, _, _ in part_entries] + deleted_keys),
            entity_label_overrides,
        )
        allowed_prefixes = normalize_allowed_prefixes(source_path, keys)
        header_text = chunk_header(
            source_path,
            idx,
            total,
            keys,
            sub_part=sub_label,
            allowed_prefixes=allowed_prefixes,
        )
        out = chunks_dir / f"{chunk_id(rel_diff, idx, total)}.txt"
        if out.exists():
            continue
        atomic_write_text(out, header_text + body_out)
        written += 1
    return written


def level1_raw(
    rel_diff: str,
    source_path: str,
    body: str,
    entity_tag: str,
    id_attribute: str,
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
    entity_label_overrides: dict[str, str] | None = None,
) -> int:
    """Level-1 split for .added/.deleted raw-file oversize artifacts."""
    intervals = build_entity_intervals(body, entity_tag, id_attribute)
    preamble = _preamble_key(entity_tag)
    lines = body.splitlines(keepends=True)

    # Cover gaps with the preamble bucket. Produce ordered chunks of raw
    # line-ranges keyed by entity.
    groups: list[tuple[str, str]] = []
    cursor = 1  # 1-based
    for start, end, key in intervals:
        if start > cursor:
            pre_text = "".join(lines[cursor - 1:start - 1])
            if pre_text.strip():
                groups.append((preamble, pre_text))
        entity_text = "".join(lines[start - 1:end])
        groups.append((key, entity_text))
        cursor = end + 1
    if cursor <= len(lines):
        tail = "".join(lines[cursor - 1:])
        if tail.strip():
            groups.append((preamble, tail))

    expanded: list[tuple[str, str, str | None]] = []
    interval_by_key = {key: (start, end) for start, end, key in intervals}
    effective_max_bytes = min(max_bytes, profile.max_body_bytes_per_chunk)
    for key, entity_text in groups:
        display_key = _translate_entity_key(key, entity_label_overrides)
        needs_split = False
        try:
            _ensure_group_fits(display_key, entity_text, max_bytes, profile)
        except OversizeGroupError:
            needs_split = True
        if key == preamble or not needs_split:
            expanded.append((key, entity_text, None))
            continue
        interval = interval_by_key.get(key)
        child_intervals = build_child_intervals(body, interval[0], interval[1]) if interval else []
        if not child_intervals:
            expanded.append((key, entity_text, None))
            continue
        child_preamble = f"{key}:{_PREAMBLE_KEY_SUFFIX}"
        child_groups: list[tuple[str, str]] = []
        child_cursor = interval[0]
        for child_start, child_end, child_key in child_intervals:
            if child_start > child_cursor:
                pre_text = "".join(lines[child_cursor - 1:child_start - 1])
                if pre_text.strip():
                    child_groups.append((child_preamble, pre_text))
            child_text = "".join(lines[child_start - 1:child_end])
            child_groups.append((child_key, child_text))
            child_cursor = child_end + 1
        if child_cursor <= interval[1]:
            tail = "".join(lines[child_cursor - 1:interval[1]])
            if tail.strip():
                child_groups.append((child_preamble, tail))

        sub_parts = pack_groups(child_groups, max_bytes, profile)
        child_tag = _child_tag_from_key(child_intervals[0][2])
        total_sub = len(sub_parts)
        for idx, part_entries in enumerate(sub_parts, start=1):
            coherent = "".join(part_body for _, part_body in part_entries)
            expanded.append((
                key,
                coherent,
                f"{idx}/{total_sub} of {display_key} (split at <{child_tag}> boundaries)",
            ))

    parts = _pack_expanded_groups(
        expanded,
        max_bytes,
        profile,
        weight_key_resolver=lambda key: _translate_entity_key(key, entity_label_overrides),
    )
    total = len(parts)
    written = 0
    for idx, part in enumerate(parts, start=1):
        sub_label = next((s for _, _, s in part if s is not None), None)
        keys_in_part = _translate_entity_keys([k for k, _, _ in part], entity_label_overrides)
        body_out = "".join(b for _, b, _ in part)
        allowed_prefixes = normalize_allowed_prefixes(source_path, keys_in_part)
        header_text = chunk_header(
            source_path,
            idx,
            total,
            keys_in_part,
            sub_part=sub_label,
            allowed_prefixes=allowed_prefixes,
        )
        out = chunks_dir / f"{chunk_id(rel_diff, idx, total)}.txt"
        if out.exists():
            continue
        atomic_write_text(out, header_text + body_out)
        written += 1
    return written


# --------------------------------------------------------------------------- #
# Level-2: generic XML split
# --------------------------------------------------------------------------- #

# Strips comments/CDATA before scanning for tag events.
_COMMENT_OR_CDATA_RE = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>", re.DOTALL)
_TAG_EVENT_RE = re.compile(r"<([/!?])?([^>]*)>")


def _xml_line_scan(text: str, start_depth: int) -> tuple[int, set[int]]:
    """Scan one XML-bearing line.

    Returns `(end_depth, returned_depths)` where `returned_depths` contains each
    depth the line returned to from above. That preserves one-line sibling
    structure such as `<cue>...</cue>` and `<row/>`, which both end at the same
    depth they started.
    """
    cleaned = _COMMENT_OR_CDATA_RE.sub("", text)
    depth = start_depth
    returned_depths: set[int] = set()
    for m in _TAG_EVENT_RE.finditer(cleaned):
        prefix = m.group(1)
        body = m.group(2)
        if prefix in ("!", "?"):
            continue
        if prefix == "/":
            depth -= 1
            returned_depths.add(depth)
        elif body.rstrip().endswith("/"):
            returned_depths.add(depth)
        else:
            depth += 1
    return depth, returned_depths


def _xml_cut_boundaries(rows: list[tuple[int, int, set[int]]]) -> list[int]:
    """Return boundary line numbers where a new structural slice should start."""
    if not rows:
        return []

    boundary_depth = _xml_boundary_depth(rows)
    return [line_no + 1 for line_no, _, returned in rows if boundary_depth in returned]


def _xml_boundary_depth(rows: list[tuple[int, int, set[int]]]) -> int:
    """Return the depth that structural siblings return to."""
    if not rows:
        return 0

    opening_depth = next((end_depth for _, end_depth, _ in rows if end_depth > 0), rows[0][1])
    candidates = rows[:-1] if len(rows) > 1 and rows[-1][1] < opening_depth else rows
    if not candidates:
        candidates = rows

    returned_depths = [depth for _, _, returned in candidates for depth in returned]
    return min(returned_depths) if returned_depths else min(end_depth for _, end_depth, _ in candidates)


def _xml_cut_lines_raw(text: str) -> tuple[list[int], int]:
    """For a raw XML file, return (cut_line_numbers, boundary_depth)."""
    lines = text.splitlines(keepends=True)
    depth = 0
    rows: list[tuple[int, int, set[int]]] = []
    for idx, line in enumerate(lines, start=1):
        depth, returned_depths = _xml_line_scan(line, depth)
        rows.append((idx, depth, returned_depths))
    if not rows:
        return [], 0

    cuts = _xml_cut_boundaries(rows)
    return cuts, _xml_boundary_depth(rows)


def _xml_line_start_depths(text: str) -> list[int]:
    """Return 1-based XML start depths for each source line plus EOF."""
    start_depths = [0]
    depth = 0
    for line in text.splitlines(keepends=True):
        start_depths.append(depth)
        depth, _returned_depths = _xml_line_scan(line, depth)
    start_depths.append(depth)
    return start_depths


def _xml_visible_rows_for_diff(
    hunks: list[tuple[int, int, str]],
    v2_source_text: str = "",
) -> list[tuple[int, int, set[int]]]:
    """Scan V2-visible rows in diff hunks, seeded from full-file V2 depth."""
    line_start_depths = _xml_line_start_depths(v2_source_text) if v2_source_text else []
    visible_rows: list[tuple[int, int, set[int]]] = []
    for _, _, htext in hunks:
        hunk_rows = htext.splitlines(keepends=True)
        if not hunk_rows:
            continue
        m = _HUNK_RE.match(hunk_rows[0].rstrip("\n"))
        if not m:
            continue
        v2 = int(m.group(3))
        depth = line_start_depths[v2] if 0 < v2 < len(line_start_depths) else 0
        for row in hunk_rows[1:]:
            if row.startswith(("+", " ")):
                depth, returned_depths = _xml_line_scan(row[1:], depth)
                visible_rows.append((v2, depth, returned_depths))
                v2 += 1
            # '-' rows don't advance V2; don't contribute to V2 depth.
    return visible_rows


def level2_xml_raw(
    rel_diff: str,
    source_path: str,
    body: str,
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
) -> int:
    """Level-2 split for a raw .added/.deleted XML file with no schema entry."""
    lines = body.splitlines(keepends=True)
    if not lines:
        return 0
    cuts, _min_depth = _xml_cut_lines_raw(body)
    # Cuts list indicates where a new chunk *starts* (line numbers, 1-based).
    # Build segments: [1..cuts[0]-1], [cuts[0]..cuts[1]-1], ...
    segment_starts = [1] + [c for c in cuts if 1 < c <= len(lines)]
    segment_starts = sorted(set(segment_starts))
    segments: list[tuple[int, int, str]] = []
    for i, start in enumerate(segment_starts):
        end = segment_starts[i + 1] - 1 if i + 1 < len(segment_starts) else len(lines)
        seg_text = "".join(lines[start - 1:end])
        if seg_text:
            segments.append((start, end, seg_text))
    if not segments:
        # Degenerate: no cut points found. Fall back to one chunk per blank-line
        # block (force-split style) so level 5 doesn't trip on pure XML with
        # no structural depth change.
        return level4_force_raw(rel_diff, source_path, body, max_bytes, chunks_dir, profile)

    header_entities = _generic_semantic_entities(source_path, "xml")
    allowed_prefixes = normalize_allowed_prefixes(source_path, header_entities) if header_entities else None
    groups = [(f"lines:{s}-{e}", text) for s, e, text in segments]
    parts = pack_groups(groups, max_bytes, profile)
    return _write_generic_parts(
        chunks_dir,
        rel_diff,
        source_path,
        parts,
        profile,
        allowed_prefixes=allowed_prefixes,
        header_entities=header_entities or None,
    )


def level2_xml_diff(
    rel_diff: str,
    source_path: str,
    body: str,
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
    v2_source_text: str = "",
) -> int:
    """Level-2 split for a .diff oversize XML artifact with no schema entry.

    Walk hunks' V2-visible rows (` ` context and `+` additions), tracking
    depth. Cut points are V2 line numbers where the visible XML returns to the
    current sibling/container boundary, even when a child opens and closes on
    the same line. Then use split_hunk_at_boundaries to slice hunks at those
    boundaries and pack.
    """
    header, file_lines, _, hunks = split_unified_diff(body)
    header_entities = _generic_semantic_entities(source_path, "xml")
    allowed_prefixes = normalize_allowed_prefixes(source_path, header_entities) if header_entities else None
    if not hunks:
        # No hunks to cut — fall back to one-chunk emit.
        return _write_single_generic(
            chunks_dir,
            rel_diff,
            source_path,
            body,
            profile,
            allowed_prefixes=allowed_prefixes,
            entities=header_entities or None,
        )

    # Pass 1: compute V2 depth and structural returns at each visible V2 line.
    visible_rows = _xml_visible_rows_for_diff(hunks, v2_source_text)
    cut_lines = _xml_cut_boundaries(visible_rows)

    if not cut_lines:
        # No structural cut points: pack whole hunks as-is, linearly.
        return _pack_hunks_linearly(
            rel_diff,
            source_path,
            header,
            file_lines,
            hunks,
            max_bytes,
            chunks_dir,
            profile,
            allowed_prefixes=allowed_prefixes,
            header_entities=header_entities or None,
        )

    # Slice each hunk at V2 cut boundaries.
    sliced: list[tuple[int, int, str]] = []
    for hunk in hunks:
        hs, he, _ = hunk
        pieces = split_hunk_at_boundaries(hunk[2], cut_lines)
        sliced.extend(pieces if pieces else [hunk])

    return _pack_hunks_linearly(
        rel_diff,
        source_path,
        header,
        file_lines,
        sliced,
        max_bytes,
        chunks_dir,
        profile,
        allowed_prefixes=allowed_prefixes,
        header_entities=header_entities or None,
    )


def _pack_hunks_linearly(
    rel_diff: str,
    source_path: str,
    header: str,
    file_lines: str,
    hunks: list[tuple[int, int, str]],
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
    allowed_prefixes: list[str] | None = None,
    header_entities: list[str] | None = None,
) -> int:
    """Pack a list of hunks into chunks, each ≤ max_bytes when possible.

    Entity labels are V2 line ranges covering each part's hunks. No entity
    keys — generic splitters don't know any.
    """
    if not hunks:
        return 0
    groups: list[tuple[str, str]] = []
    for hs, he, htext in hunks:
        label = f"lines:{hs}-{he}"
        body_piece = build_mini_diff(header, file_lines, [(hs, he, htext)])
        groups.append((label, body_piece))
    parts = pack_groups(groups, max_bytes, profile)
    return _write_generic_parts(
        chunks_dir,
        rel_diff,
        source_path,
        parts,
        profile,
        allowed_prefixes=allowed_prefixes,
        header_entities=header_entities,
    )


# --------------------------------------------------------------------------- #
# Level-3: Lua split
# --------------------------------------------------------------------------- #

_LUA_FUNC_RE = re.compile(r"^\s*(?:local\s+)?function\s+([A-Za-z_][\w.:]*)")


def _lua_function_boundaries(text: str) -> list[tuple[int, int, str]]:
    """Return [(start_line, end_line, entity_key)] segmenting a Lua file.

    Cut at blank lines (run of 1+ blank rows) and at lines starting with
    `function ` or `local function `. Each segment gets keyed by the last
    function name seen, or `lua:__top__` before the first function.
    """
    lines = text.splitlines(keepends=True)
    segments: list[tuple[int, int, str]] = []
    cur_start = 1
    cur_key = "lua:__top__"
    prev_was_blank = False

    def flush(end_line: int):
        nonlocal cur_start
        if end_line >= cur_start:
            segments.append((cur_start, end_line, cur_key))
        cur_start = end_line + 1

    for idx, row in enumerate(lines, start=1):
        stripped = row.strip()
        is_blank = stripped == ""
        fn_match = _LUA_FUNC_RE.match(row)
        if fn_match and idx > cur_start:
            # New function boundary: close previous segment (ending on previous line).
            flush(idx - 1)
            cur_key = f"lua:{fn_match.group(1)}"
        elif fn_match:
            cur_key = f"lua:{fn_match.group(1)}"
        elif is_blank and not prev_was_blank and idx > cur_start:
            # Blank-line boundary: close segment but keep the same key for the
            # next one (it's still within the same function until another def).
            flush(idx - 1)
        prev_was_blank = is_blank
    if cur_start <= len(lines):
        segments.append((cur_start, len(lines), cur_key))
    return segments


def _assign_lua_diff_hunks(
    body: str,
    v1_source_text: str = "",
    v2_source_text: str = "",
) -> list[tuple[str, tuple[int, int, str]]]:
    """Assign Lua diff hunks using both V1 and V2 function boundaries when available."""
    _, _, _, hunks = split_unified_diff(body)
    if not hunks:
        return []

    v1_intervals = _lua_function_boundaries(v1_source_text) if v1_source_text else []
    v2_intervals = _lua_function_boundaries(v2_source_text) if v2_source_text else []
    if not v1_intervals and not v2_intervals:
        return []

    preamble = "lua:__top__"
    assignments: list[tuple[str, tuple[int, int, str]]] = []
    for _core_start, _core_end, hunk_text in hunks:
        assignments.extend(
            split_modified_hunk_by_entities(
                hunk_text,
                v1_intervals,
                v2_intervals,
                preamble,
            )
        )
    return assignments


def level3_lua(
    rel_diff: str,
    source_path: str,
    body: str,
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
    is_diff: bool,
    v2_source_text: str = "",
    v1_source_text: str = "",
) -> int:
    """Level-3 split for Lua (.lua) oversize artifacts — .diff or raw."""
    if is_diff:
        header, file_lines, _, hunks = split_unified_diff(body)
        if not hunks:
            return _write_single_generic(chunks_dir, rel_diff, source_path, body, profile)
        assignments = _assign_lua_diff_hunks(
            body,
            v1_source_text=v1_source_text,
            v2_source_text=v2_source_text,
        )
        if not assignments:
            # Fallback for callers without source-side boundaries: keep the
            # older hunk-local function guess so diff chunking remains usable.
            groups: list[tuple[str, str]] = []
            for hs, he, htext in hunks:
                fn_key = _key_for_lua_hunk(htext) or "lua:__top__"
                label = f"{fn_key}:lines:{hs}-{he}"
                body_piece = build_mini_diff(header, file_lines, [(hs, he, htext)])
                groups.append((label, body_piece))
            parts = pack_groups(groups, max_bytes, profile)
            return _write_generic_parts(chunks_dir, rel_diff, source_path, parts, profile)

        preamble = "lua:__top__"
        # Use both source sides so deleted/renamed functions stay attributed to
        # the removed symbol instead of drifting to the next surviving function.
        grouped = group_hunks_by_entity(assignments)
        groups = [
            (key, build_mini_diff(header, file_lines, group_hunks))
            for key, group_hunks in grouped
        ]
        parts = pack_groups(groups, max_bytes, profile)

        total = len(parts) or 1
        written = 0
        for idx, part in enumerate(parts, start=1):
            keys = [key for key, _ in part if key != preamble]
            body_out = "".join(body_piece for _, body_piece in part)
            header_text = chunk_header(
                source_path,
                idx,
                total,
                keys,
                allowed_prefixes=normalize_allowed_prefixes(source_path, keys),
            )
            complexity_warning = _complexity_overflow_warning(body_out, keys, profile)
            if complexity_warning:
                header_text = _insert_header_warnings(header_text, [complexity_warning])
            out = chunks_dir / f"{chunk_id(rel_diff, idx, total)}.txt"
            if out.exists():
                continue
            atomic_write_text(out, header_text + body_out)
            written += 1
        return written

    # Raw (.added / .deleted): segment at functions and blank lines.
    lines = body.splitlines(keepends=True)
    segments = _lua_function_boundaries(body)
    groups = [
        (f"{key}:lines:{s}-{e}", "".join(lines[s - 1:e]))
        for s, e, key in segments
        if "".join(lines[s - 1:e]).strip()
    ]
    if not groups:
        return 0
    parts = pack_groups(groups, max_bytes, profile)
    return _write_generic_parts(chunks_dir, rel_diff, source_path, parts, profile)


def _key_for_lua_hunk(hunk_text: str) -> str | None:
    """Look inside a hunk for the nearest function-def row and return lua:NAME."""
    last = None
    for row in hunk_text.splitlines():
        if not row or row[0] not in ("+", " "):
            continue
        m = _LUA_FUNC_RE.match(row[1:])
        if m:
            last = f"lua:{m.group(1)}"
    return last


# --------------------------------------------------------------------------- #
# Level-4: force-split (line-boundary only, --force-split)
# --------------------------------------------------------------------------- #

_XML_CLOSE_TAG_RE = re.compile(r"</[A-Za-z][\w:.-]*\s*>\s*$")


def _force_cut_lines(text: str) -> list[int]:
    """Blank lines or lines that are just a closing XML tag → cut candidates."""
    cuts: list[int] = []
    for idx, row in enumerate(text.splitlines(keepends=True), start=1):
        stripped = row.strip()
        if stripped == "" or _XML_CLOSE_TAG_RE.match(stripped):
            cuts.append(idx + 1)  # next line starts a new segment
    return cuts


def _force_cut_boundaries_for_diff_hunk(hunk_text: str) -> list[int]:
    """Return V2 line numbers where a force-split diff hunk may be cut.

    Level 4 is line-based, not hunk-based: if a single diff hunk is too dense,
    we still want to cut after visible blank lines or closing-tag rows inside it.
    """
    lines = hunk_text.splitlines(keepends=True)
    if not lines:
        return []
    match = _HUNK_RE.match(lines[0].rstrip("\n"))
    if not match:
        return []

    v2 = int(match.group(3))
    cuts: list[int] = []
    for row in lines[1:]:
        if row.startswith(("+", " ")):
            stripped = row[1:].strip()
            if stripped == "" or _XML_CLOSE_TAG_RE.match(stripped):
                cuts.append(v2 + 1)
            v2 += 1
    return cuts


def _split_hunk_by_changed_line_budget(
    hunk_text: str,
    max_changed_lines: int,
) -> list[tuple[int, int, str]]:
    """Split a diff hunk at plain row boundaries to stay within the change budget."""
    lines = hunk_text.splitlines(keepends=True)
    if not lines:
        return []
    match = _HUNK_RE.match(lines[0].rstrip("\n"))
    if not match:
        return []

    v1 = int(match.group(1))
    v2 = int(match.group(3))
    rows: list[tuple[int, int, str]] = []
    for row in lines[1:]:
        if row.startswith("+"):
            rows.append((v1, v2, row))
            v2 += 1
        elif row.startswith("-"):
            rows.append((v1, v2, row))
            v1 += 1
        elif row.startswith(" "):
            rows.append((v1, v2, row))
            v1 += 1
            v2 += 1
        elif row.startswith("\\"):
            rows.append((v1, v2, row))

    if not rows:
        return []

    parts: list[list[tuple[int, int, str]]] = []
    current: list[tuple[int, int, str]] = []
    current_changed_lines = 0
    for row in rows:
        row_is_change = row[2].startswith(("+", "-"))
        if current and row_is_change and current_changed_lines >= max_changed_lines:
            parts.append(current)
            current = []
            current_changed_lines = 0
        current.append(row)
        if row_is_change:
            current_changed_lines += 1
    if current:
        parts.append(current)

    out: list[tuple[int, int, str]] = []
    for part_rows in parts:
        if not any(r[2].startswith(("+", "-")) for r in part_rows):
            continue
        v1_rows = [r for r in part_rows if r[2].startswith((" ", "-"))]
        v2_rows = [r for r in part_rows if r[2].startswith((" ", "+"))]
        hv1 = v1_rows[0][0] if v1_rows else part_rows[0][0]
        hv2 = v2_rows[0][1] if v2_rows else part_rows[0][1]
        body = "".join(r[2] for r in part_rows)
        pm_rows = [r for r in part_rows if r[2].startswith(("+", "-"))]
        core_lo = pm_rows[0][1]
        core_hi = pm_rows[-1][1]
        new_header = f"@@ -{hv1},{len(v1_rows)} +{hv2},{len(v2_rows)} @@\n"
        out.append((core_lo, core_hi, new_header + body))
    return out


def level4_force_raw(
    rel_diff: str,
    source_path: str,
    body: str,
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
) -> int:
    """Level-4 force-split for raw .added/.deleted. Cuts at blank or closing-tag lines."""
    lines = body.splitlines(keepends=True)
    cuts = sorted({1, *[c for c in _force_cut_lines(body) if 1 < c <= len(lines)]})
    segments: list[tuple[int, str]] = []
    for i, start in enumerate(cuts):
        end = cuts[i + 1] - 1 if i + 1 < len(cuts) else len(lines)
        seg = "".join(lines[start - 1:end])
        if seg.strip():
            segments.append((start, seg))
    if not segments:
        segments = [(1, body)]
    header_entities = _generic_semantic_entities(source_path, "xml" if source_path.lower().endswith(".xml") else "")
    allowed_prefixes = normalize_allowed_prefixes(source_path, header_entities) if header_entities else None
    groups = [(f"lines:{s}-{s + seg.count(chr(10))}", seg) for s, seg in segments]
    parts = pack_groups(groups, max_bytes, profile)
    return _write_generic_parts(
        chunks_dir,
        rel_diff,
        source_path,
        parts,
        profile,
        force_split=True,
        allowed_prefixes=allowed_prefixes,
        header_entities=header_entities or None,
    )


def level4_force_diff(
    rel_diff: str,
    source_path: str,
    body: str,
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
) -> int:
    """Level-4 force-split for a .diff: prefer soft cut rows, then hard row splits."""
    header, file_lines, _, hunks = split_unified_diff(body)
    header_entities = _generic_semantic_entities(source_path, "xml" if source_path.lower().endswith(".xml") else "")
    allowed_prefixes = normalize_allowed_prefixes(source_path, header_entities) if header_entities else None
    if not hunks:
        return _write_single_generic(
            chunks_dir,
            rel_diff,
            source_path,
            body,
            profile,
            force_split=True,
            allowed_prefixes=allowed_prefixes,
            entities=header_entities or None,
        )
    sliced_hunks: list[tuple[int, int, str]] = []
    for hunk in hunks:
        preferred = split_hunk_at_boundaries(hunk[2], _force_cut_boundaries_for_diff_hunk(hunk[2]))
        for piece in preferred if preferred else [hunk]:
            budgeted = _split_hunk_by_changed_line_budget(
                piece[2],
                profile.max_changed_lines_per_chunk,
            )
            sliced_hunks.extend(budgeted if budgeted else [piece])
    groups = [
        (f"lines:{hs}-{he}", build_mini_diff(header, file_lines, [(hs, he, ht)]))
        for hs, he, ht in sliced_hunks
    ]
    parts = pack_groups(groups, max_bytes, profile)
    return _write_generic_parts(
        chunks_dir,
        rel_diff,
        source_path,
        parts,
        profile,
        force_split=True,
        allowed_prefixes=allowed_prefixes,
        header_entities=header_entities or None,
    )


# --------------------------------------------------------------------------- #
# Generic-part writer (used by levels 2/3/4)
# --------------------------------------------------------------------------- #

_FORCE_WARNING = "# WARNING: force-split — cut at line boundaries, not structural"


def _insert_header_warnings(header_text: str, warnings: list[str]) -> str:
    """Insert each warning line right after the '# Chunk:' line."""
    if not warnings:
        return header_text
    lines = header_text.rstrip("\n").split("\n")
    for i, w in enumerate(warnings, start=1):
        lines.insert(i, w)
    return "\n".join(lines) + "\n"


def _write_generic_parts(
    chunks_dir: Path,
    rel_diff: str,
    source_path: str,
    parts: list[list[tuple[str, str]]],
    profile: ChunkProfile,
    *,
    force_split: bool = False,
    allowed_prefixes: list[str] | None = None,
    header_entities: list[str] | None = None,
) -> int:
    """Emit chunk files from a pre-packed groups list."""
    total = len(parts) or 1
    written = 0
    for idx, part in enumerate(parts, start=1):
        keys = [k for k, _ in part]
        body_out = "".join(b for _, b in part)
        display_entities = header_entities if header_entities is not None else keys
        header_text = chunk_header(
            source_path,
            idx,
            total,
            display_entities,
            allowed_prefixes=allowed_prefixes,
        )
        warnings: list[str] = []
        if force_split:
            warnings.append(_FORCE_WARNING)
        complexity_warning = _complexity_overflow_warning(body_out, keys, profile)
        if complexity_warning:
            warnings.append(complexity_warning)
        header_text = _insert_header_warnings(header_text, warnings)
        out = chunks_dir / f"{chunk_id(rel_diff, idx, total)}.txt"
        if out.exists():
            continue
        atomic_write_text(out, header_text + body_out)
        written += 1
    return written


def _write_single_generic(
    chunks_dir: Path,
    rel_diff: str,
    source_path: str,
    body: str,
    profile: ChunkProfile,
    *,
    force_split: bool = False,
    allowed_prefixes: list[str] | None = None,
    entities: list[str] | None = None,
) -> int:
    """Write a single part 1/1 chunk. Used when a splitter finds no cut points."""
    cid = chunk_id(rel_diff, 1, 1)
    out = chunks_dir / f"{cid}.txt"
    if out.exists():
        return 0
    header_text = chunk_header(source_path, 1, 1, entities or [], allowed_prefixes=allowed_prefixes)
    warnings: list[str] = []
    if force_split:
        warnings.append(_FORCE_WARNING)
    complexity_warning = _complexity_overflow_warning(body, entities or [], profile)
    if complexity_warning:
        warnings.append(complexity_warning)
    header_text = _insert_header_warnings(header_text, warnings)
    atomic_write_text(out, header_text + body)
    return 1


# --------------------------------------------------------------------------- #
# Level-5: hard fail
# --------------------------------------------------------------------------- #

def level5_fail(source_path: str, size: int, max_bytes: int, tried: str) -> None:
    raise SystemExit(
        f"[03_chunk] FAIL: {source_path} is {size} B (> {max_bytes} B) "
        f"and no splitter could handle it. Tried: {tried}.\n"
        f"  Options: raise --chunk-kb, exclude the file, add it to "
        f"src/x4_schema_map.generated.json (then rerun rescan-schema), set --force-split, "
        f"or implement a custom splitter."
    )


def fail_unsplittable_group(source_path: str, err: OversizeGroupError) -> None:
    raise SystemExit(
        f"[03_chunk] FAIL: {source_path} has a chunk group that won't fit the "
        f"LLM context ({err.key}): {err.size} B > byte budget {err.max_bytes} B.\n"
        f"  Options: raise --chunk-kb, add a more specific splitter/schema hint, "
        f"set --force-split, or implement a custom splitter."
    )


def run_level4_force_split(
    ext: str,
    rel_diff: str,
    source_path: str,
    body: str,
    max_bytes: int,
    chunks_dir: Path,
    profile: ChunkProfile,
) -> int:
    if ext == ".diff":
        return level4_force_diff(rel_diff, source_path, body, max_bytes, chunks_dir, profile)
    if ext in (".added", ".deleted"):
        return level4_force_raw(rel_diff, source_path, body, max_bytes, chunks_dir, profile)
    raise SystemExit(f"[03_chunk] unsupported extension {ext} for {source_path}.")


def _file_kind(source_path: str) -> str:
    """xml, lua, or other — dispatched from the file extension."""
    ext = Path(source_path).suffix.lower()
    if ext == ".xml":
        return "xml"
    if ext == ".lua":
        return "lua"
    return "other"


def _resolve_xml_entity_info(
    source_path: str,
    xml_text: str,
    entity_info: tuple[str, str] | None,
    label_prefix: str | None = None,
    v1_xml_text: str = "",
) -> tuple[tuple[str, str] | None, dict[str, str]]:
    if entity_info is not None:
        overrides: dict[str, str] = {}
        if label_prefix is not None:
            tag, attr = entity_info
            # Union cues from V1 and V2 so entities present on only one side still remap.
            for src_text in (xml_text, v1_xml_text):
                if not src_text:
                    continue
                for _, _, key in build_entity_intervals(src_text, tag, attr):
                    if key and key.startswith(f"{tag}:"):
                        _, id_value = key.split(":", 1)
                        overrides[key] = f"{label_prefix}:{id_value}"
            preamble = _preamble_key(tag)
            overrides[preamble] = f"{label_prefix}:{_PREAMBLE_KEY_SUFFIX}"
        return entity_info, overrides

    macro_info = parse_singleton_macro(source_path, xml_text)
    if macro_info is None:
        return None, {}

    raw_key = f"macro:{macro_info.macro_name}"
    return ("macro", "name"), {raw_key: resolve_macro_prefix(macro_info)}


def _lua_keys_from_raw(text: str) -> list[str]:
    keys = [key for _, _, key in _lua_function_boundaries(text)]
    return _dedupe_preserve_order([key for key in keys if key != "lua:__top__"])


def _lua_keys_from_diff(
    body: str,
    v2_source_text: str = "",
    v1_source_text: str = "",
) -> list[str]:
    assignments = _assign_lua_diff_hunks(
        body,
        v1_source_text=v1_source_text,
        v2_source_text=v2_source_text,
    )
    if assignments:
        grouped = group_hunks_by_entity(assignments)
        return [key for key, _ in grouped if key != "lua:__top__"]
    _, _, _, hunks = split_unified_diff(body)
    keys = [_key_for_lua_hunk(htext) for _, _, htext in hunks]
    return _dedupe_preserve_order([key for key in keys if key and key != "lua:__top__"])


def infer_chunk_labels(
    source_path: str,
    ext: str,
    kind: str,
    body: str,
    entity_info: tuple[str, str] | None,
    entity_label_overrides: dict[str, str] | None = None,
    v2_source_text: str = "",
    v1_source_text: str = "",
) -> tuple[list[str], list[str]]:
    """Return (display_entities, allowed_prefixes) for a chunk.

    Small chunks use this directly so they keep specific labels instead of
    collapsing to "entire file". Generic splitters can also reuse the returned
    allowed-prefix list when their display entities are line-oriented.
    """
    display_entities: list[str] = []
    if ext == ".diff":
        if kind == "xml" and v2_source_text:
            if is_dlc_diff_file(v2_source_text):
                intervals = build_dlc_diff_intervals(v2_source_text)
                display_entities = touched_entity_keys_for_diff(body, intervals, _preamble_key("diff"))
            else:
                if entity_info is not None:
                    tag, attr = entity_info
                    intervals = build_entity_intervals(v2_source_text, tag, attr)
                    display_entities = touched_entity_keys_for_diff(body, intervals, _preamble_key(tag))
                    if v1_source_text:
                        deleted_intervals = build_entity_intervals(v1_source_text, tag, attr)
                        display_entities = _dedupe_preserve_order(
                            display_entities
                            + touched_deleted_entity_keys_for_diff(
                                body,
                                deleted_intervals,
                                _preamble_key(tag),
                            ),
                        )
        elif kind == "lua":
            display_entities = _lua_keys_from_diff(body, v2_source_text, v1_source_text)
    elif ext in (".added", ".deleted"):
        if kind == "xml":
            if entity_info is not None:
                tag, attr = entity_info
                display_entities = _entity_keys_from_intervals(build_entity_intervals(body, tag, attr))
        elif kind == "lua":
            display_entities = _lua_keys_from_raw(body)

    if not display_entities:
        display_entities = _generic_semantic_entities(source_path, kind)

    display_entities = _translate_entity_keys(display_entities, entity_label_overrides)
    return display_entities, normalize_allowed_prefixes(source_path, display_entities)


# --------------------------------------------------------------------------- #
# Happy path (≤ CHUNK_KB)
# --------------------------------------------------------------------------- #

def emit_single_chunk(
    chunks_dir: Path,
    rel_diff: str,
    source_path: str,
    body: str,
    entities: list[str],
    allowed_prefixes: list[str],
) -> bool:
    """Emit a part 1/1 chunk. Returns True if written, False if already existed."""
    cid = chunk_id(rel_diff, 1, 1)
    out = chunks_dir / f"{cid}.txt"
    if out.exists():
        return False
    atomic_write_text(
        out,
        chunk_header(source_path, 1, 1, entities=entities, allowed_prefixes=allowed_prefixes) + body,
    )
    return True


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def _resolve_schema_entry(schema_map: dict, source_path: str) -> tuple[str, str] | None:
    if source_path in schema_map:
        return schema_map[source_path]
    stripped = strip_dlc_prefix(source_path)
    if stripped != source_path and stripped in schema_map:
        return schema_map[stripped]
    basename = Path(source_path).name
    for key, val in schema_map.items():
        if Path(key).name == basename:
            return val
    return None


def main():
    p = argparse.ArgumentParser(description="Pack diffs into LLM-ready chunks.")
    p.add_argument("--v1", required=True, type=Path)
    p.add_argument("--v2", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--chunk-kb", type=int, required=True)
    p.add_argument("--force-split", action="store_true")
    p.add_argument(
        "--schema-map",
        type=Path,
        default=None,
        help="Path to a schema-map JSON file (default: src/x4_schema_map.generated.json next to this script).",
    )
    args = p.parse_args()

    max_bytes = args.chunk_kb * 1024
    diffs_dir = args.out / "02_diff" / "diffs"
    chunks_dir = args.out / "03_chunk" / "chunks"

    schema_map_path = args.schema_map or (Path(__file__).parent / DEFAULT_SCHEMA_MAP_FILENAME)
    schema_map = load_schema_map(schema_map_path)

    diff_paths = [
        p for p in sorted(diffs_dir.rglob("*"))
        if p.is_file() and p.suffix in _DIFF_EXTS
    ]
    progress = Progress("03_chunk", len(diff_paths))

    written = skipped = 0
    for diff_path in diff_paths:
        rel_diff = diff_path.relative_to(diffs_dir).as_posix()
        source_path = source_path_from_diff(rel_diff)
        ext = diff_path.suffix
        body = diff_path.read_text(encoding="utf-8", errors="replace")
        size = len(body.encode("utf-8"))
        kind = _file_kind(source_path)
        profile = chunk_profile_for_source_path(source_path)
        override = file_entity_override(source_path)
        if override is not None:
            o_tag, o_attr, label_prefix = override
            schema_entity_info: tuple[str, str] | None = (o_tag, o_attr)
        else:
            schema_entity_info = _resolve_schema_entry(schema_map, source_path)
            label_prefix = None

        v2_text = ""
        v1_text = ""
        dlc_patch = False
        if ext == ".diff":
            v1_path = args.v1 / source_path
            v2_path = args.v2 / source_path
            v1_text = v1_path.read_text(encoding="utf-8", errors="replace") if v1_path.exists() else ""
            v2_text = v2_path.read_text(encoding="utf-8", errors="replace") if v2_path.exists() else ""
            dlc_patch = bool(v2_text) and is_dlc_diff_file(v2_text)

        resolved_xml_entity_info: tuple[str, str] | None = None
        entity_label_overrides: dict[str, str] = {}
        if kind == "xml":
            if ext == ".diff" and not dlc_patch and v2_text:
                resolved_xml_entity_info, entity_label_overrides = _resolve_xml_entity_info(
                    source_path,
                    v2_text,
                    schema_entity_info,
                    label_prefix=label_prefix,
                    v1_xml_text=v1_text,
                )
            elif ext in (".added", ".deleted"):
                resolved_xml_entity_info, entity_label_overrides = _resolve_xml_entity_info(
                    source_path,
                    body,
                    schema_entity_info,
                    label_prefix=label_prefix,
                )

        attempted_level4_force_split = False
        try:
            if size <= max_bytes:
                entities, allowed_prefixes = infer_chunk_labels(
                    source_path,
                    ext,
                    kind,
                    body,
                    resolved_xml_entity_info,
                    entity_label_overrides,
                    v2_text,
                    v1_text,
                )
                if not chunk_is_too_complex(body, count_entities(entities), profile):
                    if emit_single_chunk(chunks_dir, rel_diff, source_path, body, entities, allowed_prefixes):
                        progress.tick(f"{source_path} → 1 chunk")
                        written += 1
                    else:
                        progress.tick(f"skip {source_path}")
                        skipped += 1
                    continue

                progress.tick(f"{source_path} ({size}B) → complexity splitter")
            else:
                progress.tick(f"{source_path} ({size}B) → splitter")

            if ext == ".diff":
                if dlc_patch:
                    n = level1_modified(
                        rel_diff, source_path, body, v1_text, v2_text,
                        entity_tag="diff", id_attribute="",
                        max_bytes=max_bytes, chunks_dir=chunks_dir, profile=profile, is_dlc=True,
                        entity_label_overrides=entity_label_overrides,
                    )
                elif resolved_xml_entity_info is not None and kind == "xml":
                    entity_tag, id_attribute = resolved_xml_entity_info
                    n = level1_modified(
                        rel_diff, source_path, body, v1_text, v2_text,
                        entity_tag=entity_tag, id_attribute=id_attribute,
                        max_bytes=max_bytes, chunks_dir=chunks_dir, profile=profile,
                        entity_label_overrides=entity_label_overrides,
                    )
                elif kind == "xml":
                    n = level2_xml_diff(
                        rel_diff,
                        source_path,
                        body,
                        max_bytes,
                        chunks_dir,
                        profile,
                        v2_source_text=v2_text,
                    )
                elif kind == "lua":
                    n = level3_lua(
                        rel_diff,
                        source_path,
                        body,
                        max_bytes,
                        chunks_dir,
                        profile,
                        is_diff=True,
                        v2_source_text=v2_text,
                        v1_source_text=v1_text,
                    )
                elif args.force_split:
                    attempted_level4_force_split = True
                    n = run_level4_force_split(
                        ext,
                        rel_diff,
                        source_path,
                        body,
                        max_bytes,
                        chunks_dir,
                        profile,
                    )
                else:
                    level5_fail(source_path, size, max_bytes, tried="L1/L2/L3")
                written += n
                continue

            if ext in (".added", ".deleted"):
                if resolved_xml_entity_info is not None and kind == "xml":
                    entity_tag, id_attribute = resolved_xml_entity_info
                    n = level1_raw(
                        rel_diff, source_path, body,
                        entity_tag=entity_tag, id_attribute=id_attribute,
                        max_bytes=max_bytes, chunks_dir=chunks_dir, profile=profile,
                        entity_label_overrides=entity_label_overrides,
                    )
                elif kind == "xml":
                    n = level2_xml_raw(rel_diff, source_path, body, max_bytes, chunks_dir, profile)
                elif kind == "lua":
                    n = level3_lua(rel_diff, source_path, body, max_bytes, chunks_dir, profile, is_diff=False)
                elif args.force_split:
                    attempted_level4_force_split = True
                    n = run_level4_force_split(
                        ext,
                        rel_diff,
                        source_path,
                        body,
                        max_bytes,
                        chunks_dir,
                        profile,
                    )
                else:
                    level5_fail(source_path, size, max_bytes, tried="L1/L2/L3")
                written += n
                continue

            raise SystemExit(f"[03_chunk] unsupported extension {ext} for {source_path}.")
        except OversizeGroupError as err:
            if args.force_split and not attempted_level4_force_split:
                progress.same(f"{source_path} → force-split fallback ({err.key})")
                try:
                    n = run_level4_force_split(
                        ext,
                        rel_diff,
                        source_path,
                        body,
                        max_bytes,
                        chunks_dir,
                        profile,
                    )
                except OversizeGroupError as force_err:
                    fail_unsplittable_group(source_path, force_err)
                written += n
                continue
            fail_unsplittable_group(source_path, err)

    progress.log(f"wrote {written}, skipped {skipped}")


if __name__ == "__main__":
    main()
