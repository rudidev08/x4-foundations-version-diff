#!/usr/bin/env python3
"""Prepare diff batches for LLM analysis.

Usage:
    python3 diff-tools/prepare_diff_analysis.py 8.00H4 9.00B1

Reads diffs from diff/{V1}-{V2}/ and produces concatenated batch files
in diff/{V1}-{V2}/_analysis/_batches/ with a manifest.json for agent orchestration.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIFF_DIR = ROOT / "diff"

MAX_BATCH_KB = 50

# Cosmetic/visual domains to skip by default
SKIP_DOMAINS = {
    "assets/environments",
    "assets/fx",
    "shadergl",
    "assets/interiors",
    "assets/characters",
    "assets/cutscenecore",
    "assets/legacy",
    "assets/system",
    "assets/map",
    "assets/ui",
    "cutscenes",
}

# Visual-heavy library files to skip (huge diffs, no gameplay impact)
SKIP_LIBRARY_FILES = {
    "effects.xml",
    "effects.xsd",
    "material_library.xml",
    "material_library_1.xml",
    "material_library.xsd",
    "envmapprobes.xml",
    "renderparam_library.xml",
}

DOMAIN_LABELS = {
    "libraries": "Game data libraries (economy, balance, factions, ships, jobs, modules)",
    "aiscripts": "AI behavior scripts (orders, combat, trading, mining, movement)",
    "md": "Mission Director scripts (missions, faction logic, story, game systems)",
    "assets/units": "Ship/drone definitions (hull, thrust, mass, crew, storage)",
    "assets/props": "Equipment definitions (engines, weapons, shields, turrets, scanners)",
    "assets/structures": "Station module definitions (production, habitats, defense, docking)",
    "assets/wares": "Ware 3D models (visual representations of tradeable items)",
    "maps": "Universe layout (sectors, zones, clusters, highways)",
    "t": "Localization text (English) — new/changed game text",
    "ui": "UI framework and menus (Lua scripts, HUD elements)",
    "index": "Master lookup tables (macro/component name-to-file mappings)",
}


def classify_domain(rel_path: str) -> str:
    parts = Path(rel_path).parts
    if parts[0] == "extensions":
        dlc = parts[1] if len(parts) > 1 else "unknown"
        if len(parts) > 2:
            subdir = parts[2]
            if subdir == "assets" and len(parts) > 3:
                return f"extensions/{dlc}/assets/{parts[3]}"
            return f"extensions/{dlc}/{subdir}"
        return f"extensions/{dlc}"
    if parts[0] == "assets" and len(parts) > 1:
        return f"assets/{parts[1]}"
    return parts[0]


def should_skip_domain(domain: str) -> bool:
    if domain in SKIP_DOMAINS:
        return True
    parts = domain.split("/")
    if len(parts) >= 4 and f"{parts[2]}/{parts[3]}" in SKIP_DOMAINS:
        return True
    return False


def should_skip_file(rel_path: str) -> bool:
    """Skip asset component files (geometry) and visual library files."""
    parts = Path(rel_path).parts
    filename = parts[-1]

    # Skip visual-heavy library files
    if "libraries" in parts and filename in SKIP_LIBRARY_FILES:
        return True

    # In assets/, only keep macro files (gameplay stats), skip components (geometry)
    # Macros live in macros/ subdirectories; everything else is geometry/visuals
    if "assets" in parts:
        asset_subdirs = {"units", "props", "structures"}
        # Check if this is an asset type we filter
        for i, p in enumerate(parts):
            if p in asset_subdirs:
                # Keep if "macros" is anywhere in the remaining path
                remaining = parts[i + 1:]
                if "macros" not in remaining:
                    return True
                break

    return False


def get_domain_label(domain: str) -> str:
    if domain in DOMAIN_LABELS:
        return DOMAIN_LABELS[domain]
    parts = domain.split("/")
    if parts[0] == "extensions" and len(parts) >= 3:
        dlc_name = parts[1].replace("ego_dlc_", "DLC: ")
        sub = "/".join(parts[2:])
        sub_label = DOMAIN_LABELS.get(sub, sub)
        return f"{dlc_name} — {sub_label}"
    return domain


def main():
    parser = argparse.ArgumentParser(description="Prepare diff batches for LLM analysis")
    parser.add_argument("v1", help="Old version (e.g. 8.00H4)")
    parser.add_argument("v2", help="New version (e.g. 9.00B1)")
    parser.add_argument("--max-kb", type=int, default=MAX_BATCH_KB,
                        help=f"Max batch size in KB (default: {MAX_BATCH_KB})")
    parser.add_argument("--include-cosmetic", action="store_true",
                        help="Include cosmetic domains (environments, fx, shaders, etc.)")
    args = parser.parse_args()

    diff_dir = DIFF_DIR / f"{args.v1}-{args.v2}"
    if not diff_dir.is_dir():
        sys.exit(f"Diff directory not found: {diff_dir}\nRun version_diff.py first.")

    batch_dir = diff_dir / "_analysis" / "_batches"
    if batch_dir.exists():
        for f in batch_dir.rglob("*"):
            if f.is_file():
                f.unlink()
    batch_dir.mkdir(parents=True, exist_ok=True)

    # Collect and group diffs
    groups = defaultdict(list)
    skipped_domains = defaultdict(lambda: {"count": 0, "bytes": 0})

    for diff_file in sorted(diff_dir.rglob("*.diff")):
        if "_analysis" in diff_file.parts:
            continue
        rel = str(diff_file.relative_to(diff_dir))
        orig_rel = rel.removesuffix(".diff")
        domain = classify_domain(orig_rel)

        if not args.include_cosmetic and should_skip_domain(domain):
            skipped_domains[domain]["count"] += 1
            skipped_domains[domain]["bytes"] += diff_file.stat().st_size
            continue

        if not args.include_cosmetic and should_skip_file(orig_rel):
            skipped_domains[domain + " (geometry/visual)"]["count"] += 1
            skipped_domains[domain + " (geometry/visual)"]["bytes"] += diff_file.stat().st_size
            continue

        groups[domain].append((orig_rel, diff_file))

    # Create batch files
    manifest = []
    max_bytes = args.max_kb * 1024

    for domain in sorted(groups):
        files = groups[domain]
        batches = []
        current_batch = []
        current_size = 0

        for orig_rel, diff_file in files:
            content = diff_file.read_text(encoding="utf-8")
            header = f"{'=' * 80}\nFILE: {orig_rel}\n{'=' * 80}\n"
            entry = header + content + "\n"
            entry_size = len(entry.encode("utf-8"))

            if current_size + entry_size > max_bytes and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_size = 0

            current_batch.append(entry)
            current_size += entry_size

        if current_batch:
            batches.append(current_batch)

        for i, batch in enumerate(batches):
            if len(batches) == 1:
                filename = domain.replace("/", "--") + ".diff"
            else:
                filename = f"{domain.replace('/', '--')}--part{i + 1}.diff"

            batch_path = batch_dir / filename
            batch_path.write_text("".join(batch), encoding="utf-8")

            manifest.append({
                "file": filename,
                "domain": domain,
                "label": get_domain_label(domain),
                "part": i + 1 if len(batches) > 1 else None,
                "total_parts": len(batches) if len(batches) > 1 else None,
                "diff_count": len(batch),
                "size_kb": round(batch_path.stat().st_size / 1024, 1),
            })

    manifest_path = batch_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Print summary
    print(f"Batches: {batch_dir}/\n")

    total_kb = 0
    for entry in manifest:
        parts_info = f" (part {entry['part']}/{entry['total_parts']})" if entry["part"] else ""
        print(f"  {entry['file']:55s} {entry['diff_count']:4d} diffs  {entry['size_kb']:8.1f} KB  {parts_info}")
        total_kb += entry["size_kb"]

    print(f"\n  {'TOTAL':55s} {sum(e['diff_count'] for e in manifest):4d} diffs  {total_kb:8.1f} KB")
    print(f"  Batches: {len(manifest)}")

    if skipped_domains:
        skipped_total = sum(d["count"] for d in skipped_domains.values())
        skipped_kb = sum(d["bytes"] for d in skipped_domains.values()) / 1024
        print(f"\n  Skipped (cosmetic): {skipped_total} diffs, {skipped_kb:.0f} KB")
        for domain in sorted(skipped_domains):
            d = skipped_domains[domain]
            print(f"    {domain}: {d['count']} diffs, {d['bytes'] / 1024:.0f} KB")


if __name__ == "__main__":
    main()
