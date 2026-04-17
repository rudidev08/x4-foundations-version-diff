from __future__ import annotations

from pathlib import Path


ID_ATTRIBUTE_CANDIDATES = ("id", "name", "macro")
VANILLA_SCAN_SUBDIRS = ("libraries", "md", "maps")
DLC_SCAN_SUBDIRS = ("libraries", "md", "maps")


def iter_scan_roots(source: Path):
    """Yield (absolute_dir, rel_prefix) pairs that the X4 schema scan should walk."""
    for subdir in VANILLA_SCAN_SUBDIRS:
        directory = source / subdir
        if directory.is_dir():
            yield directory, subdir

    extensions_dir = source / "extensions"
    if not extensions_dir.is_dir():
        return
    for dlc_dir in sorted(extensions_dir.iterdir()):
        if not dlc_dir.is_dir() or not dlc_dir.name.startswith("ego_dlc_"):
            continue
        for subdir in DLC_SCAN_SUBDIRS:
            directory = dlc_dir / subdir
            if directory.is_dir():
                yield directory, f"extensions/{dlc_dir.name}/{subdir}"


def choose_repeating_child_entity(
    root_tag: str | None,
    child_counts: dict[str, int],
    child_attr_presence: dict[str, dict[str, int]],
) -> tuple[str, str] | None:
    """Pick the best repeating direct child tag plus its id-bearing attribute."""
    if root_tag == "diff":
        return None

    candidates: list[tuple[int, str, str]] = []
    for tag, count in child_counts.items():
        if count < 2:
            continue
        attr_counts = child_attr_presence.get(tag, {})
        for attr in ID_ATTRIBUTE_CANDIDATES:
            if attr_counts.get(attr, 0) == count:
                candidates.append((count, tag, attr))
                break

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _, tag, attr = candidates[0]
    return tag, attr
