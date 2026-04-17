#!/usr/bin/env python3
"""
Pipeline step 05 — group findings by entity + category, write the changelog.

Usage:
    python3 src/05_assemble.py --out DIR \\
        --v1-name NAME --v2-name NAME --model NAME [--strict-findings] \\
        --changelog PATH

Input:
    <--out>/03_chunk/chunks/*.txt    (enumerates chunk IDs; missing findings = failed)
    <--out>/04_llm/findings/*.md     (the findings themselves)

Output:
    <--changelog>   Markdown changelog. See spec.md → "Changelog format".

Resumability:
    Pure function of existing chunks/findings. Always safe to re-run;
    the output file is overwritten via atomic rename.
"""
import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from _lib import (
    ALLOWED_PREFIXES_LINE_PREFIX,
    CHUNK_HEADER_SEPARATOR_PREFIX,
    Progress,
    atomic_write_text,
    file_fallback_prefix,
)
from x4_rules_categories import categorize, category_order

_PREFIX_LINE_RE = re.compile(r"^\[([^\]]+)\]$")
_FENCE_LINE_RE = re.compile(r"^\s*```[\w-]*\s*$")
_CHUNK_HEADER_RE = re.compile(r"^# Chunk:\s+(.*) part \d+/\d+\s*$")
_ENTITIES_RE = re.compile(r"^# Entities(?: \(\d+\))?:\s*(.*)$")
_MORE_ENTITIES_SUFFIX_RE = re.compile(r", \+\d+ more$")
_SUB_PART_RE = re.compile(r"^# Sub-part:\s*(.*)$")
_SPECULATIVE_RE = re.compile(r"\b(suggests?|appears?|seems?|likely|unlikely|probably|perhaps|maybe)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "this", "that", "these", "those",
    "their", "there", "then", "than", "while", "when", "where", "which", "using",
    "used", "through", "after", "before", "under", "over", "into", "onto", "also",
    "still", "just", "have", "has", "had", "now", "new", "section", "script",
    "behaviour", "behavior", "ships", "ship", "objects", "object",
})


@dataclass(frozen=True)
class ChunkMeta:
    source_path: str
    entities: tuple[str, ...]
    allowed_prefixes: tuple[str, ...]
    sub_part: str | None


@dataclass(frozen=True)
class FindingBlock:
    raw_prefix: str | None
    body: str
    line: int


@dataclass(frozen=True)
class NormalizedFinding:
    prefix: str
    body: str
    malformed: bool
    error: dict[str, object] | None


@dataclass(frozen=True)
class BucketFinding:
    body: str
    chunk_id: str
    sub_part: str | None


@dataclass(frozen=True)
class PointCandidate:
    text: str
    order: int
    normalized: str
    tokens: frozenset[str]
    speculative: bool


def parse_entities_line(line: str) -> tuple[str, ...]:
    if line.strip() == "# Entities: entire file":
        return ()
    m = _ENTITIES_RE.match(line.strip())
    if not m:
        return ()
    payload = _MORE_ENTITIES_SUFFIX_RE.sub("", m.group(1).strip())
    if not payload or payload == "entire file":
        return ()
    return tuple(part.strip() for part in payload.split(",") if part.strip())


def parse_allowed_prefixes_line(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if not stripped.startswith(ALLOWED_PREFIXES_LINE_PREFIX):
        return ()
    payload = stripped[len(ALLOWED_PREFIXES_LINE_PREFIX):].strip()
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        return ()
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, str) and item)


def parse_chunk_meta(chunk_file: Path) -> ChunkMeta:
    """Peek the chunk header to recover the original source path and shown entities."""
    if not chunk_file.exists():
        return ChunkMeta(source_path="", entities=(), allowed_prefixes=(), sub_part=None)
    source_path = ""
    entities: tuple[str, ...] = ()
    allowed_prefixes: tuple[str, ...] = ()
    sub_part: str | None = None
    for line in chunk_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(CHUNK_HEADER_SEPARATOR_PREFIX):
            break
        m = _CHUNK_HEADER_RE.match(line)
        if m:
            source_path = m.group(1)
            continue
        m = _SUB_PART_RE.match(line)
        if m:
            sub_part = m.group(1).strip() or None
            continue
        if line.startswith("# Entities"):
            entities = parse_entities_line(line)
            continue
        if line.startswith(ALLOWED_PREFIXES_LINE_PREFIX):
            allowed_prefixes = parse_allowed_prefixes_line(line)
    return ChunkMeta(
        source_path=source_path,
        entities=entities,
        allowed_prefixes=allowed_prefixes,
        sub_part=sub_part,
    )


def entity_display(prefix: str, source_path: str) -> str:
    if prefix:
        return prefix
    return f"({source_path})" if source_path else "(unknown)"


def is_none_finding(finding_body: str) -> bool:
    nonempty = [line.strip() for line in finding_body.splitlines() if line.strip()]
    return nonempty == ["[none]"]


def strip_fence_wrappers(body: str) -> str:
    # Some LLMs wrap their entire response in a ``` markdown block; the fences
    # are never real content, so drop them before parsing prefixes/bullets.
    return "\n".join(line for line in body.splitlines() if not _FENCE_LINE_RE.match(line))


def split_finding_blocks(finding_body: str) -> list[FindingBlock]:
    blocks: list[FindingBlock] = []
    preamble: list[str] = []
    preamble_line: int | None = None
    current_prefix: str | None = None
    current_line: int | None = None
    current_body: list[str] = []

    for idx, line in enumerate(finding_body.splitlines(), start=1):
        stripped = line.strip()
        m = _PREFIX_LINE_RE.match(stripped)
        if m:
            if current_prefix is not None:
                blocks.append(FindingBlock(current_prefix, "\n".join(current_body).strip(), current_line or idx))
            elif any(part.strip() for part in preamble):
                blocks.append(FindingBlock(None, "\n".join(preamble).strip(), preamble_line or idx))
            preamble = []
            preamble_line = None
            current_prefix = m.group(1)
            current_line = idx
            current_body = []
            continue

        if current_prefix is None:
            if preamble_line is None and stripped:
                preamble_line = idx
            preamble.append(line)
        else:
            current_body.append(line)

    if current_prefix is not None:
        blocks.append(FindingBlock(current_prefix, "\n".join(current_body).strip(), current_line or 1))
    elif any(part.strip() for part in preamble):
        blocks.append(FindingBlock(None, "\n".join(preamble).strip(), preamble_line or 1))

    return blocks


def expected_prefixes(meta: ChunkMeta) -> tuple[str, ...]:
    if meta.allowed_prefixes:
        return meta.allowed_prefixes
    expected: list[str] = []
    for entity in meta.entities:
        if entity == "entire file" or entity.startswith("lines:"):
            continue
        expected.append(entity)
    if meta.source_path:
        expected.append(file_fallback_prefix(meta.source_path))
    return tuple(expected)


def should_normalize_to_file_fallback(raw_prefix: str | None) -> bool:
    if raw_prefix is None:
        return True
    if raw_prefix.startswith("file:"):
        return True
    if raw_prefix.startswith("lines:"):
        return True
    return ":" not in raw_prefix


def normalize_finding(block: FindingBlock, meta: ChunkMeta, finding_path: Path) -> NormalizedFinding:
    fallback = file_fallback_prefix(meta.source_path)
    expected = expected_prefixes(meta)

    malformed = False
    reason = ""
    if block.raw_prefix is None:
        malformed = True
        prefix = fallback
        reason = "missing [entity:key] prefix"
    elif block.raw_prefix in expected:
        prefix = block.raw_prefix
    else:
        malformed = True
        if should_normalize_to_file_fallback(block.raw_prefix):
            prefix = fallback
        else:
            prefix = block.raw_prefix
        if block.raw_prefix.startswith("file:"):
            reason = f"invalid file fallback; expected [{fallback}]"
        elif block.raw_prefix.startswith("lines:"):
            reason = "line-range pseudo-entity is not a valid output prefix; use the file fallback"
        elif ":" not in block.raw_prefix:
            reason = "prefix is not a valid entity:key token; use the file fallback"
        else:
            reason = "prefix not allowed for this chunk; strict mode rejects it even though tolerant mode keeps it"

    if not block.body:
        malformed = True
        if reason:
            reason = f"{reason}; empty finding body"
        else:
            reason = "empty finding body"

    error: dict[str, object] | None = None
    if malformed:
        error = {
            "finding_file": finding_path.as_posix(),
            "source_path": meta.source_path,
            "line": block.line,
            "reason": reason,
            "got": f"[{block.raw_prefix}]" if block.raw_prefix is not None else None,
            "rendered_as": prefix,
            "expected_prefixes": list(expected),
            "rendered": bool(block.body),
        }

    return NormalizedFinding(prefix=prefix, body=block.body, malformed=malformed, error=error)


def split_body_points(body: str) -> list[str]:
    points: list[str] = []
    prose: list[str] = []
    current: list[str] = []
    saw_bullets = False

    for line in body.splitlines():
        if line.startswith("- "):
            saw_bullets = True
            if prose:
                preamble = "\n".join(prose).strip()
                if preamble:
                    points.append(preamble)
                prose = []
            if current:
                points.append("\n".join(current).strip())
            current = [line[2:]]
            continue
        if saw_bullets:
            current.append(line.rstrip())
        else:
            prose.append(line.rstrip())

    if current:
        points.append("\n".join(current).strip())
    elif prose:
        prose_block = "\n".join(prose).strip()
        if prose_block:
            points.append(prose_block)
    return [point for point in points if point]


def _normalize_point_text(text: str) -> str:
    lowered = text.lower().replace("`", "")
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9_ ]+", " ", lowered)
    return " ".join(lowered.split())


def _content_tokens(text: str) -> frozenset[str]:
    return frozenset(
        token for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    )


def _point_overlap(a: PointCandidate, b: PointCandidate) -> float:
    if not a.tokens or not b.tokens:
        return 0.0
    return len(a.tokens & b.tokens) / min(len(a.tokens), len(b.tokens))


def _shared_token_count(a: PointCandidate, b: PointCandidate) -> int:
    return len(a.tokens & b.tokens)


def _point_quality(point: PointCandidate) -> int:
    numeric_bonus = 5 if re.search(r"\d", point.text) else 0
    speculative_penalty = 20 if point.speculative else 0
    return len(point.text) + numeric_bonus - speculative_penalty


def _prefer_point(a: PointCandidate, b: PointCandidate) -> PointCandidate:
    better = a if _point_quality(a) >= _point_quality(b) else b
    order = min(a.order, b.order)
    if better.order == order:
        return better
    return PointCandidate(
        text=better.text,
        order=order,
        normalized=better.normalized,
        tokens=better.tokens,
        speculative=better.speculative,
    )


def _render_point(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    rendered = ["- " + lines[0]]
    rendered.extend(lines[1:])
    return "\n".join(rendered).rstrip()


def condense_label_findings(findings: list[BucketFinding]) -> str:
    candidates: list[PointCandidate] = []
    for finding in findings:
        for point in split_body_points(finding.body):
            normalized = _normalize_point_text(point)
            if not normalized:
                continue
            candidates.append(PointCandidate(
                text=point,
                order=len(candidates),
                normalized=normalized,
                tokens=_content_tokens(point),
                speculative=bool(_SPECULATIVE_RE.search(point)),
            ))

    if not candidates:
        return ""

    deduped: list[PointCandidate] = []
    seen_normalized: set[str] = set()
    for point in candidates:
        if point.normalized in seen_normalized:
            continue
        deduped.append(point)
        seen_normalized.add(point.normalized)

    any_subpart = any(finding.sub_part for finding in findings)
    survivors: list[PointCandidate] = []
    for point in deduped:
        absorbed = False
        for idx, existing in enumerate(survivors):
            overlap = _point_overlap(point, existing)
            if overlap >= 0.9:
                survivors[idx] = _prefer_point(existing, point)
                absorbed = True
                break
            if (any_subpart or len(findings) > 1) and overlap >= 0.8:
                survivors[idx] = _prefer_point(existing, point)
                absorbed = True
                break
            if (any_subpart or len(findings) > 1) and (
                overlap >= 0.45 or _shared_token_count(point, existing) >= 4
            ) and (
                point.speculative or existing.speculative
            ):
                if point.speculative != existing.speculative:
                    survivors[idx] = existing if not existing.speculative else point
                else:
                    survivors[idx] = _prefer_point(existing, point)
                absorbed = True
                break
        if not absorbed:
            survivors.append(point)

    survivors.sort(key=lambda point: point.order)
    return "\n".join(
        rendered for rendered in (_render_point(point.text) for point in survivors)
        if rendered
    )


def format_strict_error(report_path: Path, malformed: list[dict[str, object]]) -> str:
    lines = [
        f"05_assemble: ERROR: {len(malformed)} malformed findings found; changelog not written.",
        f"See {report_path}.",
        "",
    ]
    for entry in malformed[:10]:
        lines.append(f"{entry['finding_file']}:{entry['line']}")
        lines.append(f"  got:      {entry['got'] or '(missing prefix)'}")
        lines.append(f"  expected: {', '.join(entry['expected_prefixes']) or '(no valid prefixes)'}")
        lines.append(f"  reason:   {entry['reason']}")
        lines.append("")
    if len(malformed) > 10:
        lines.append(f"... and {len(malformed) - 10} more malformed findings.")
    return "\n".join(lines).rstrip() + "\n"


def main():
    p = argparse.ArgumentParser(description="Group findings into the final changelog.")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--v1-name", required=True)
    p.add_argument("--v2-name", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--strict-findings", action="store_true",
                   help="Abort if any finding block lacks a valid [entity:key] prefix.")
    p.add_argument("--changelog", required=True, type=Path)
    args = p.parse_args()

    chunks_dir = args.out / "03_chunk" / "chunks"
    findings_dir = args.out / "04_llm" / "findings"
    malformed_report = args.out / "05_assemble" / "malformed_findings.jsonl"

    chunk_ids = {p.stem for p in chunks_dir.glob("*.txt")}
    finding_ids = {p.stem for p in findings_dir.glob("*.md")}
    failed_count = len(chunk_ids - finding_ids)

    buckets: dict[str, dict[str, list[BucketFinding]]] = defaultdict(lambda: defaultdict(list))
    malformed_findings: list[dict[str, object]] = []
    total_findings = 0
    for fid in sorted(finding_ids):
        finding_path = findings_dir / f"{fid}.md"
        body = strip_fence_wrappers(finding_path.read_text(encoding="utf-8")).strip()
        if not body or is_none_finding(body):
            continue
        meta = parse_chunk_meta(chunks_dir / f"{fid}.txt")
        for block in split_finding_blocks(body):
            normalized = normalize_finding(block, meta, finding_path.relative_to(args.out))
            if normalized.error is not None:
                error = {
                    "chunk_id": fid,
                    **normalized.error,
                }
                malformed_findings.append(error)
            if not normalized.body:
                continue
            category = categorize(normalized.prefix, meta.source_path)
            label = entity_display(normalized.prefix, meta.source_path)
            buckets[category][label].append(BucketFinding(
                body=normalized.body,
                chunk_id=fid,
                sub_part=meta.sub_part,
            ))
            total_findings += 1

    report_text = "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in malformed_findings)
    atomic_write_text(malformed_report, report_text)

    if args.strict_findings and malformed_findings:
        raise SystemExit(format_strict_error(malformed_report, malformed_findings))

    parts = [
        f"# Changelog: {args.v1_name} → {args.v2_name}\n",
        "\n",
        f"Generated by {args.model} on {date.today().isoformat()}.\n",
        "\n",
    ]
    for category in category_order():
        if category not in buckets:
            continue
        parts.append(f"## {category}\n\n")
        for label in sorted(buckets[category]):
            rendered_body = condense_label_findings(buckets[category][label])
            if not rendered_body:
                continue
            parts.append(f"### {label}\n")
            parts.append(rendered_body + "\n")
            parts.append("\n")
    parts.append("---\n")
    parts.append(
        f"{args.model} | {len(chunk_ids)} chunks | {total_findings} findings | "
        f"{len(malformed_findings)} malformed findings tolerated | {failed_count} failed chunks\n"
    )
    atomic_write_text(args.changelog, "".join(parts))
    Progress("05_assemble", 1).log(
        f"{args.changelog} ({total_findings} findings, "
        f"{len(malformed_findings)} malformed tolerated, {failed_count} failed chunks)"
    )


if __name__ == "__main__":
    main()
