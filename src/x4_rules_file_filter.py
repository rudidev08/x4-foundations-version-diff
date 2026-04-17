from __future__ import annotations

import os
from pathlib import Path


EXCLUDED_EXTENSIONS = {
    ".xsd", ".js", ".css", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".gif", ".tga", ".bmp", ".dds",
    ".wav", ".ogg", ".mp3", ".mp4",
    ".exe", ".dll", ".so", ".dylib",
    ".xac", ".xsm", ".ani", ".bin",
}

EXCLUDED_TOP_LEVEL_DIRS = {"shadergl", "index", "cutscenes"}


def strip_dlc_prefix(rel_path: str) -> str:
    path = rel_path.replace("\\", "/")
    if path.startswith("extensions/"):
        parts = path.split("/", 2)
        if len(parts) == 3 and parts[1].startswith("ego_dlc_"):
            return parts[2]
    return path


def normalize_source_path(rel_path: str) -> str:
    return strip_dlc_prefix(rel_path).lower()


def should_include(rel_path: str) -> bool:
    """Decide whether a relative source path is gameplay-relevant for X4."""
    path = strip_dlc_prefix(rel_path)
    normalized = path.lower()

    if os.path.splitext(normalized)[1] in EXCLUDED_EXTENSIONS:
        return False

    base = os.path.basename(normalized)
    if base.startswith(("material_library", "sound_library", "sound_env_library")):
        return False

    top = normalized.split("/", 1)[0]
    if top in EXCLUDED_TOP_LEVEL_DIRS:
        return False

    if normalized.startswith(("libraries/", "md/", "aiscripts/", "ui/", "maps/")):
        return True
    if normalized == "ui.xml":
        return True
    if normalized == "t/0001-l044.xml":
        return True
    if normalized.startswith("assets/"):
        parts = normalized.split("/")
        if len(parts) >= 4 and parts[1] in {"units", "props", "structures", "fx"} and "macros" in parts:
            return True
        return False
    return False


def walk_filtered(root: Path):
    """Yield POSIX-style relative paths under `root` that pass `should_include`."""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir != "." and _is_pruned_dir(rel_dir):
            dirnames[:] = []
            continue
        for name in filenames:
            full = Path(dirpath) / name
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if should_include(rel):
                yield rel


def _is_pruned_dir(rel_dir: str) -> bool:
    normalized = normalize_source_path(rel_dir)
    top = normalized.split("/", 1)[0]
    return top in EXCLUDED_TOP_LEVEL_DIRS
