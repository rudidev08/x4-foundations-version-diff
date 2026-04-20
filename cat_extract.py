#!/usr/bin/env python3
"""Extract X4 Foundations .cat/.dat archives.

Usage:
    python3 cat_extract.py /path/to/X4/Foundations x4-data/<version> --all-folders
    python3 cat_extract.py /path/to/X4/Foundations ./output -f ".*"
    python3 cat_extract.py /path/to/X4/Foundations ./output --no-recursive

Recursively searches subdirectories by default, so DLC files under
extensions/ego_dlc_*/ are found and extracted into their own subfolders.

The changelog pipeline expects this extractor to be run with --all-folders.

Based on alexparlett's gist, with fixes for Linux paths containing dots.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Write payload to target atomically: write a sibling .tmp then rename.

    A keyboard interrupt mid-extraction can leave a .tmp behind; the next
    run silently overwrites it. The point is to keep partial files from
    being mistaken for completed extractions by anything downstream.
    """
    tmp = target.with_suffix(target.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(
                    f"short write to {tmp} "
                    f"({len(payload) - len(view)}/{len(payload)} bytes)"
                )
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, target)

# Only extract files from these top-level folders (plus root-level files).
# Set to None to extract all folders.
ALLOWED_FOLDERS = {
    "aiscripts",
    "index",
    "libraries",
    "maps",
    "md",
    "t",
}


def is_path_allowed(filepath: str) -> bool:
    """Check if filepath is in an allowed folder or is a root-level file."""
    if ALLOWED_FOLDERS is None:
        return True

    parts = filepath.split("/")

    # Root-level file (no folder)
    if len(parts) == 1:
        return True

    return parts[0] in ALLOWED_FOLDERS


def find_cat_files(sourcedir: Path, recursive: bool = True,
                   include: list[str] | None = None) -> list[Path]:
    """Find .cat files in sourcedir, optionally recursing into subdirectories."""
    cat_files: list[Path] = []

    if recursive:
        for root, _dirs, files in os.walk(sourcedir):
            for f in sorted(files):
                filepath = Path(root) / f
                if (not include and f.lower().endswith(".cat")) or (include and f in include):
                    cat_files.append(filepath)
    else:
        for f in sorted(os.listdir(sourcedir)):
            filepath = sourcedir / f
            if filepath.is_file():
                if (not include and f.lower().endswith(".cat")) or (include and f in include):
                    cat_files.append(filepath)

    return cat_files


def extract_cat_files(sourcedir: Path, destdir: Path,
                      include: list[str] | None = None,
                      file_filter: str = r'^.*\.(xml|xsd|html|js|css|lua)$',
                      recursive: bool = True) -> None:
    """Extract matching files from .cat/.dat archive pairs."""
    pattern = re.compile(file_filter)

    cat_files = find_cat_files(sourcedir, recursive, include)
    if not cat_files:
        print(f"No .cat files found in {sourcedir}")
        return

    print(f"Found {len(cat_files)} .cat file(s)")

    total_extracted = 0

    for cat_file in cat_files:
        # Fix for Linux paths with dots: use stem + suffix replacement
        dat_file = cat_file.with_suffix(".dat")

        if not dat_file.exists():
            print(f"Warning: {dat_file} not found, skipping {cat_file}")
            continue

        # Relative path of the .cat file's directory from source root.
        # Preserves folder structure (e.g., extensions/ego_dlc_split/).
        cat_rel_dir = cat_file.parent.relative_to(sourcedir)

        print(f"Processing: {cat_file.relative_to(sourcedir)}")
        extracted = 0

        with open(cat_file, "r") as cat_fh, open(dat_file, "rb") as dat_fh:
            for line in cat_fh:
                line = line.strip()
                if not line:
                    continue

                # CAT format: filepath size timestamp hash
                # Filepath can contain spaces, so split from the right
                parts = line.rsplit(" ", 3)
                if len(parts) != 4:
                    # Without a valid size we can't advance the .dat read head,
                    # so every subsequent entry would read at the wrong offset.
                    # Abort this archive rather than silently corrupt the rest.
                    print(f"  Error: malformed line: {line[:50]}...; aborting archive to prevent corruption")
                    break

                filepath, size_str, _timestamp, _filehash = parts

                try:
                    size = int(size_str)
                except ValueError:
                    print(f"  Error: invalid size '{size_str}' for {filepath}; aborting archive to prevent corruption")
                    break

                if pattern.match(filepath) and is_path_allowed(filepath):
                    out_file = destdir / cat_rel_dir / filepath
                    out_file.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        data = dat_fh.read(size)
                        if len(data) != size:
                            print(f"  Error: short read on {filepath} ({len(data)}/{size} bytes); aborting archive")
                            break
                        _atomic_write_bytes(out_file, data)
                        extracted += 1
                    except IOError as e:
                        print(f"  Error writing {out_file}: {e}")
                else:
                    # Skip this file's data in the .dat
                    dat_fh.seek(size, 1)

        print(f"  Extracted {extracted} files")
        total_extracted += extracted

    print(f"\nTotal: {total_extracted} files extracted to {destdir}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract X4 Foundations .cat/.dat archives"
    )
    parser.add_argument(
        "sourcedir",
        help="Directory containing .cat/.dat files (e.g., X4 game folder)",
    )
    parser.add_argument(
        "destdir",
        help="Output directory for extracted files",
    )
    parser.add_argument(
        "-i", "--include", nargs="*",
        help="Specific .cat filenames to extract (default: all .cat files)",
    )
    parser.add_argument(
        "-f", "--filter",
        default=r'^.*\.(xml|xsd|html|js|css|lua)$',
        help='Regex filter for files to extract (default: xml,xsd,html,js,css,lua). Use ".*" for all.',
    )
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="Only search top-level directory (default: recurse into subdirectories)",
    )
    parser.add_argument(
        "--all-folders", action="store_true",
        help="Extract from all folders, bypassing ALLOWED_FOLDERS filter. Expected for pipeline inputs.",
    )

    args = parser.parse_args()

    if args.all_folders:
        global ALLOWED_FOLDERS
        ALLOWED_FOLDERS = None

    sourcedir = Path(args.sourcedir)
    destdir = Path(args.destdir)

    if not sourcedir.is_dir():
        sys.exit(f"Error: Source directory does not exist: {sourcedir}")

    destdir.mkdir(parents=True, exist_ok=True)

    extract_cat_files(sourcedir, destdir, args.include, args.filter,
                      not args.no_recursive)


if __name__ == "__main__":
    main()
