"""Chunk raw diffs into LLM-sized batches grouped by domain.

Deterministic. No LLM calls. Input: diff/raw/{pair}/**/*.diff. Output:
diff/models/{model}/{pair}/chunks/{domain}--partN.diff plus _manifest.json.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


# Domains skipped entirely — visual/cosmetic, no gameplay signal worth the LLM call.
SKIP_DOMAINS = {
    "assets/environments", "assets/fx", "shadergl", "assets/interiors",
    "assets/characters", "assets/cutscenecore", "assets/legacy",
    "assets/system", "assets/map", "assets/ui", "cutscenes",
}

# Within libraries/, these are high-volume low-signal engine config files.
SKIP_LIBRARY_FILES = {
    "effects.xml", "effects.xsd", "material_library.xml",
    "material_library_1.xml", "material_library.xsd",
    "envmapprobes.xml", "renderparam_library.xml",
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
    "t": "Localization text (English)",
    "ui": "UI framework and menus (Lua scripts, HUD elements)",
    "index": "Master lookup tables (macro/component name-to-file mappings)",
}


def classify_domain(rel_path: str) -> str:
    """Map a relative diff path to a domain bucket.

    Extensions get their own domain (extensions/{dlc}/{subdir}) so a base-game
    library change and a DLC library change don't end up in the same chunk.
    """
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
    if parts[0] == "extensions" and len(parts) >= 3:
        sub = "/".join(parts[2:])
        if sub in SKIP_DOMAINS:
            return True
    return False


def should_skip_file(rel_path: str) -> bool:
    """Per-file skip rules for files inside included domains."""
    parts = Path(rel_path).parts
    filename = parts[-1]
    if "libraries" in parts and filename in SKIP_LIBRARY_FILES:
        return True
    # Under assets/{units,props,structures}, only macros/ carry gameplay stats;
    # components/ is pure geometry noise.
    if "assets" in parts:
        for i, p in enumerate(parts):
            if p in {"units", "props", "structures"}:
                if "macros" not in parts[i + 1:]:
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


def _split_into_hunks(diff_text: str, max_bytes: int) -> list[str]:
    """Split a unified diff into groups of hunks that each fit under max_bytes.

    Each returned chunk includes the original diff header so it reads standalone.
    """
    lines = diff_text.split("\n")
    hunk_starts = [i for i, line in enumerate(lines) if line.startswith("@@")]
    if not hunk_starts:
        return [diff_text]

    header_lines = lines[: hunk_starts[0]]
    header = "\n".join(header_lines) + "\n" if header_lines else ""

    raw_hunks = []
    for idx, start in enumerate(hunk_starts):
        end = hunk_starts[idx + 1] if idx + 1 < len(hunk_starts) else len(lines)
        raw_hunks.append("\n".join(lines[start:end]))

    chunks = []
    current = header
    for hunk in raw_hunks:
        candidate = current + hunk + "\n"
        if len(candidate.encode("utf-8")) > max_bytes and current != header:
            chunks.append(current)
            current = header + hunk + "\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks


def _analysis_variants(domain: str) -> list[str]:
    """Which analysis prompts this domain's chunks need.

    Localization (`t`) is analyzed twice — once for mechanics text (ware/UI
    strings), once for story/lore. Other domains get a single general pass.
    """
    if domain == "t" or domain.endswith("/t"):
        return ["mechanics", "lore"]
    return ["general"]


def prepare_chunks(raw_dir: Path, chunks_dir: Path, chunk_size_kb: int) -> list[dict]:
    """Pack raw diffs into domain-grouped chunks <= chunk_size_kb.

    Writes chunks_dir/{domain}--partN.diff and chunks_dir/_manifest.json.
    Returns the manifest (also written to disk).
    """
    max_bytes = chunk_size_kb * 1024

    # Always rebuild cleanly — chunking is cheap and any stale files confuse
    # downstream file-existence checks.
    if chunks_dir.exists():
        for f in chunks_dir.rglob("*"):
            if f.is_file():
                f.unlink()
    chunks_dir.mkdir(parents=True, exist_ok=True)

    groups: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for diff_file in sorted(raw_dir.rglob("*.diff")):
        rel = str(diff_file.relative_to(raw_dir))
        orig_rel = rel.removesuffix(".diff")
        domain = classify_domain(orig_rel)
        if should_skip_domain(domain):
            continue
        if should_skip_file(orig_rel):
            continue
        groups[domain].append((orig_rel, diff_file))

    manifest: list[dict] = []

    for domain in sorted(groups):
        files = groups[domain]
        batches: list[list[str]] = []
        current_batch: list[str] = []
        current_size = 0

        for orig_rel, diff_file in files:
            content = diff_file.read_text(encoding="utf-8")
            header = f"{'=' * 80}\nFILE: {orig_rel}\n{'=' * 80}\n"
            entry = header + content + "\n"
            entry_size = len(entry.encode("utf-8"))

            if entry_size > max_bytes:
                # Single file exceeds chunk size — split by hunk boundaries.
                hunk_chunks = _split_into_hunks(content, max_bytes - len(header.encode("utf-8")))
                for i, hunk in enumerate(hunk_chunks, 1):
                    hunk_header = f"{'=' * 80}\nFILE: {orig_rel} (hunk group {i}/{len(hunk_chunks)})\n{'=' * 80}\n"
                    hunk_entry = hunk_header + hunk + "\n"
                    hunk_size = len(hunk_entry.encode("utf-8"))
                    if current_size + hunk_size > max_bytes and current_batch:
                        batches.append(current_batch)
                        current_batch = []
                        current_size = 0
                    current_batch.append(hunk_entry)
                    current_size += hunk_size
                continue

            if current_size + entry_size > max_bytes and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_size = 0
            current_batch.append(entry)
            current_size += entry_size

        if current_batch:
            batches.append(current_batch)

        variants = _analysis_variants(domain)
        domain_slug = domain.replace("/", "--")

        for i, batch in enumerate(batches, 1):
            if len(batches) == 1:
                filename = f"{domain_slug}.diff"
                part = None
                total_parts = None
            else:
                filename = f"{domain_slug}--part{i}.diff"
                part = i
                total_parts = len(batches)

            batch_path = chunks_dir / filename
            batch_path.write_text("".join(batch), encoding="utf-8")

            manifest.append({
                "file": filename,
                "domain": domain,
                "label": get_domain_label(domain),
                "part": part,
                "total_parts": total_parts,
                "diff_count": len(batch),
                "size_kb": round(batch_path.stat().st_size / 1024, 1),
                "analysis_variants": variants,
            })

    manifest_path = chunks_dir / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest
