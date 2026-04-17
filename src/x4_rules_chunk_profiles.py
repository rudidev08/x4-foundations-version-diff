from __future__ import annotations

from dataclasses import dataclass, replace

from x4_rules_file_filter import normalize_source_path as _normalize_source_path


@dataclass(frozen=True)
class ChunkProfile:
    max_entities_per_chunk: int = 6
    max_body_bytes_per_chunk: int = 6 * 1024
    max_changed_lines_per_chunk: int = 30
    max_hunks_per_chunk: int = 3
    max_complexity_score: int = 90
    entity_weight: int = 12
    changed_line_weight: int = 1
    hunk_weight: int = 10
    subpart_weight: int = 8


DEFAULT_CHUNK_PROFILE = ChunkProfile()


_PROFILE_OVERRIDES: tuple[tuple[str, dict[str, int]], ...] = (
    # Weak local models still drop the larger job batches even after the
    # universal caps, so keep job chunks smaller than the global default.
    ("libraries/jobs.xml", {
        "max_entities_per_chunk": 3,
        "max_complexity_score": 72,
    }),
)


def normalize_source_path(source_path: str) -> str:
    return _normalize_source_path(source_path)


def chunk_profile_for_source_path(source_path: str) -> ChunkProfile:
    normalized = normalize_source_path(source_path)
    profile = DEFAULT_CHUNK_PROFILE
    for path_prefix, overrides in _PROFILE_OVERRIDES:
        if normalized == path_prefix:
            return replace(profile, **overrides)
    return profile


def complexity_score(
    *,
    profile: ChunkProfile,
    entity_count: int,
    changed_line_count: int,
    hunk_count: int,
    subpart_count: int = 1,
) -> int:
    return (
        entity_count * profile.entity_weight
        + changed_line_count * profile.changed_line_weight
        + hunk_count * profile.hunk_weight
        + max(0, subpart_count - 1) * profile.subpart_weight
    )
