#!/usr/bin/env python3
"""Unified X4 diff pipeline orchestrator.

Replaces the separate analyze/summarize/write task scripts, shell wrappers,
and prompt renderer with a single CLI. Progress is stored as JSON for easy
recovery after interruption.

Usage:
    python3 diff-tools/pipeline.py prepare V1 V2
    python3 diff-tools/pipeline.py status V1 V2
    python3 diff-tools/pipeline.py next V1 V2
    python3 diff-tools/pipeline.py done V1 V2
    python3 diff-tools/pipeline.py assemble V1 V2
    python3 diff-tools/pipeline.py reset V1 V2 [phase]
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIFF_DIR = ROOT / "diff"
RESULTS_DIR = ROOT / "diff-results"

# Load .env if present
_env_file = ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

LLM_MODEL = os.environ.get("LLM_MODEL", "")

PHASE_SUBDIRS = {"analyze": "_analysis", "summarize": "_summary", "write": "_sections"}

# --- Batch preparation config ---

MAX_BATCH_KB = 25

SKIP_DOMAINS = {
    "assets/environments", "assets/fx", "shadergl", "assets/interiors",
    "assets/characters", "assets/cutscenecore", "assets/legacy",
    "assets/system", "assets/map", "assets/ui", "cutscenes",
}

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

# --- Write phase sections ---

SECTIONS = [
    {"id": "combat", "label": "Combat System",
     "focus": "Shields, weapons, missiles, turrets, AI targeting, weapon heat, disruption mechanics",
     "domains": ["libraries", "aiscripts", "assets--props", "assets--units", "md", "extensions"]},
    {"id": "new_mechanics", "label": "New Game Systems",
     "focus": "New attributes, new gameplay features, new AI behaviors",
     "domains": ["libraries", "aiscripts", "md", "maps", "localization_mechanics", "extensions"]},
    {"id": "economy_trade", "label": "Economy & Trade",
     "focus": "Ware pricing, production recipes, trade AI, resource flow",
     "domains": ["libraries", "aiscripts", "md", "assets--structures", "extensions"]},
    {"id": "missions", "label": "Mission System",
     "focus": "Mission logic, subscriptions, rewards, faction goals",
     "domains": ["md", "localization_mechanics", "extensions"]},
    {"id": "story_lore", "label": "Story & Lore",
     "focus": "Story dialog, characters, faction lore, encyclopedia entries, tutorial narrative",
     "domains": ["localization_lore", "md", "extensions"]},
    {"id": "ui", "label": "UI & Interface",
     "focus": "Menus, HUD, panels, Lua scripts, notifications",
     "domains": ["ui", "localization_mechanics"]},
    {"id": "ship_balance", "label": "Ship Balance",
     "focus": "Hull, mass, thrust, inertia, drag, crew, storage, engine stats, physics",
     "domains": ["libraries", "assets--units", "assets--props", "extensions"]},
    {"id": "dlc", "label": "DLC-Specific",
     "focus": "Content unique to specific DLCs that doesn't fit other sections",
     "domains": ["extensions"]},
    {"id": "new_content", "label": "New Content",
     "focus": "New ships, wares, stations, story, characters, missions",
     "domains": ["*"]},
    {"id": "bug_fixes", "label": "Bug Fixes",
     "focus": "Corrected values, fixed logic, resolved issues",
     "domains": ["*"]},
    {"id": "miscellaneous", "label": "Miscellaneous",
     "focus": "Anything not covered by other sections",
     "domains": ["*"]},
]

# --- Path helpers ---


def run_dir(v1: str, v2: str) -> Path:
    """Per-model run directory. Batches are shared; outputs are per-model."""
    return DIFF_DIR / f"{v1}-{v2}" / "_runs" / LLM_MODEL


def progress_path(v1: str, v2: str) -> Path:
    return run_dir(v1, v2) / "_progress.json"


def load_progress(v1: str, v2: str) -> dict:
    p = progress_path(v1, v2)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_progress(v1: str, v2: str, data: dict) -> None:
    p = progress_path(v1, v2)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


# --- Batch preparation (from prepare_diff_analysis.py logic) ---


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
    if parts[0] == "extensions" and len(parts) >= 3:
        sub = "/".join(parts[2:])
        if sub in SKIP_DOMAINS:
            return True
    return False


def should_skip_file(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    filename = parts[-1]
    if "libraries" in parts and filename in SKIP_LIBRARY_FILES:
        return True
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


def _split_into_hunks(diff_text: str) -> list[str]:
    """Split a unified diff into groups of hunks that fit within MAX_BATCH_KB."""
    max_bytes = MAX_BATCH_KB * 1024
    lines = diff_text.split("\n")
    # Collect hunk boundaries (lines starting with @@)
    hunk_starts = [i for i, line in enumerate(lines) if line.startswith("@@")]
    if not hunk_starts:
        return [diff_text]  # No hunks found, return as-is

    # Diff header is everything before the first hunk
    header_lines = lines[:hunk_starts[0]]
    header = "\n".join(header_lines) + "\n" if header_lines else ""

    # Extract each hunk
    raw_hunks = []
    for idx, start in enumerate(hunk_starts):
        end = hunk_starts[idx + 1] if idx + 1 < len(hunk_starts) else len(lines)
        raw_hunks.append("\n".join(lines[start:end]))

    # Group hunks into chunks that fit within max_bytes
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


def prepare_batches(v1: str, v2: str) -> list[dict]:
    """Generate batched diff files grouped by domain. Returns manifest."""
    diff_dir = DIFF_DIR / f"{v1}-{v2}"
    batch_dir = diff_dir / "_batches"

    if batch_dir.exists():
        for f in batch_dir.rglob("*"):
            if f.is_file():
                f.unlink()
    batch_dir.mkdir(parents=True, exist_ok=True)

    groups: dict[str, list[tuple[str, Path]]] = defaultdict(list)

    for diff_file in sorted(diff_dir.rglob("*.diff")):
        if "_batches" in diff_file.parts or "_analysis" in diff_file.parts:
            continue
        rel = str(diff_file.relative_to(diff_dir))
        orig_rel = rel.removesuffix(".diff")
        domain = classify_domain(orig_rel)

        if should_skip_domain(domain):
            continue
        if should_skip_file(orig_rel):
            continue

        groups[domain].append((orig_rel, diff_file))

    manifest = []
    max_bytes = MAX_BATCH_KB * 1024

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

            # If single file exceeds max, split by diff hunks
            if entry_size > max_bytes:
                hunks = _split_into_hunks(content)
                hunk_num = 0
                for hunk in hunks:
                    hunk_num += 1
                    hunk_header = f"{'=' * 80}\nFILE: {orig_rel} (hunks {hunk_num})\n{'=' * 80}\n"
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
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


# --- Task list builders ---


def build_analyze_tasks(manifest: list[dict]) -> list[dict]:
    """Build analyze task list from batch manifest."""
    t_entries = [e for e in manifest if e["domain"] == "t"]
    index_entries = [e for e in manifest if e["domain"] == "index"]
    has_t = len(t_entries) > 0
    tasks = []

    for entry in manifest:
        if entry["domain"] == "t":
            continue
        if entry["domain"] == "index" and has_t:
            continue
        task_id = entry["file"].replace(".diff", "")
        tasks.append({
            "id": task_id,
            "status": "pending",
            "type": "general",
            "label": entry["label"],
            "batch_file": entry["file"],
        })

    if has_t:
        t_files = [e["file"] for e in t_entries]
        index_files = [e["file"] for e in index_entries]
        tasks.append({
            "id": "localization_mechanics",
            "status": "pending",
            "type": "localization_mechanics",
            "label": "Localization text (English) — mechanics-related changes",
            "batch_files": t_files,
            "index_batch_files": index_files,
        })
        tasks.append({
            "id": "localization_lore",
            "status": "pending",
            "type": "localization_lore",
            "label": "Localization text (English) — story and lore changes",
            "batch_files": t_files,
        })

    return tasks


def build_summarize_tasks(v1: str, v2: str) -> list[dict]:
    """Discover multi-part analysis results that need consolidation."""
    analysis_dir = run_dir(v1, v2) / "_analysis"
    groups: dict[str, list[str]] = defaultdict(list)

    for f in sorted(analysis_dir.glob("*.md")):
        m = re.match(r"^(.+?)--part(\d+)$", f.stem)
        if m:
            groups[m.group(1)].append(f.name)

    tasks = []
    for domain in sorted(groups):
        if len(groups[domain]) >= 2:
            tasks.append({
                "id": domain,
                "status": "pending",
                "file_count": len(groups[domain]),
                "files": sorted(groups[domain]),
            })

    return tasks


def build_write_tasks(v1: str, v2: str) -> list[dict]:
    """Build write task list from section definitions."""
    return [{"id": s["id"], "status": "pending"} for s in SECTIONS]


def build_dedup_tasks(v1: str, v2: str) -> list[dict]:
    """Build pairwise dedup tasks from all section combinations."""
    rdir = run_dir(v1, v2)
    sections_dir = rdir / "_sections"
    # Only include sections that exist and have content
    active = []
    for s in SECTIONS:
        f = sections_dir / f"{s['id']}.md"
        if f.exists() and f.read_text().strip() != "No changes.":
            active.append(s["id"])
    tasks = []
    for a, b in itertools.combinations(active, 2):
        tasks.append({"id": f"{a}--vs--{b}", "a": a, "b": b, "status": "pending"})
    return tasks


# --- Prompt templates ---


def prompt_analyze_general(v1: str, v2: str, label: str, batch_path: str) -> str:
    return f"""Analyze the following X4 Foundations game diff files for gameplay-relevant changes between version {v1} and {v2}. Read the batch file completely.

Domain: {label}
File to read: {batch_path}

For each change found, classify its impact:
- Critical / High Impact: combat balance, economy flow, new mechanics, ship stats, weapon behavior, AI behavior, mission structure
- Medium Impact: specific ship classes, faction-specific, tactical, UI functionality, quality-of-life
- Low Impact / Cosmetic: visual effects, sounds, code cleanup, whitespace, internal architecture

Structure your output as:
### {label} — Critical / High Impact
### {label} — Medium Impact
### {label} — Low Impact / Cosmetic

Rules:
- Include specific numeric values (old -> new) for all stat changes
- Don't skip small changes — a single number can be a major balance shift
- Note new files separately from modified files
- For XML attribute additions/removals, note what was added/removed
- When ambiguous, search source/{v2}/ and source/{v1}/ for context
- Never use markdown tables
- Use inline bullets: `Item: old -> new detail | detail | detail`

Write output to: {{output_path}}"""


def prompt_analyze_localization_mechanics(v1: str, v2: str, batch_paths: list[str], index_paths: list[str] | None) -> str:
    files_lines = [f"- {p}" for p in batch_paths]
    if index_paths:
        files_lines.extend(f"- {p}" for p in index_paths)
    files_section = "\n".join(files_lines)
    return f"""Analyze the following X4 Foundations localization and index diff files for MECHANICS-related text changes between version {v1} and {v2}.

Files to read:
{files_section}

Focus on English localization (`l044`) only. Look for:
- New or renamed wares, weapons, equipment, ships, station modules
- New or changed UI strings, menu labels, button text, notifications, warnings, settings, tooltips
- Weapon/equipment effect descriptions that describe mechanics
- New game features revealed by text
- Index changes for new/removed macro/component registrations

Exclude: story, lore, NPC dialog, mission narrative, flavor text.

Classify as High Impact, Medium Impact, Low Impact, and Index Changes. Quote actual text strings for renamed items and new mechanics. Never use markdown tables.

Write output to: {{output_path}}"""


def prompt_analyze_localization_lore(v1: str, v2: str, batch_paths: list[str]) -> str:
    files_section = "\n".join(f"- {p}" for p in batch_paths)
    return f"""Analyze the following X4 Foundations localization diff files for STORY and LORE text changes between version {v1} and {v2}.

Files to read:
{files_section}

Focus on English localization (`l044`) only. Prioritize:
- Unique stations, objects, locations
- NPC characters (new, renamed, removed)
- Mission dialog and briefings
- Faction/world lore
- Removed lore
- Tutorial/onboarding narrative
- Genuinely meaningful encyclopedia rewrites

Exclude: mechanics, UI labels, index changes, effect descriptions.

Structure as:
### Story & Lore - New Content
### Story & Lore - Rewritten Content
### Story & Lore - Removed Content
### Story & Lore - Minor Fixes

For rewrites, quote both old and new text. For broad tone-only rewrites, summarize the trend. Never use markdown tables.

Write output to: {{output_path}}"""


def prompt_summarize(v1: str, v2: str, domain: str, files: list[str]) -> str:
    file_list = "\n".join(f"- {f}" for f in files)
    return f"""Read the following domain analysis files and consolidate into a single unified summary. These may contain overlapping entries because source diffs were split into parts.

Domain: {domain}
Files to read:
{file_list}

Rules:
- Combine duplicate entries into one with ALL details from every source
- Preserve every old -> new number, stat change, named item
- Use higher impact classification when files disagree
- Merge related changes to same system/object
- Remove only exact duplicates; keep anything with unique detail
- Preserve explanatory context

Structure:
### {domain} — Critical / High Impact
### {domain} — Medium Impact
### {domain} — Low Impact / Cosmetic

Never use markdown tables. Use inline bullets: `Item: old -> new detail | detail`

Write output to: {{output_path}}"""


def prompt_write(v1: str, v2: str, label: str, focus: str, files: list[str]) -> str:
    file_list = "\n".join(f"- {f}" for f in files) if files else "- (none)"
    return f"""Read the following analysis/summary files and synthesize everything relevant to the theme into a single cohesive changelog section.

Theme: {label}
Focus: {focus}

Files to read:
{file_list}

Guidelines:
- Read every listed file completely before writing
- Lead with the most impactful changes
- Include specific old -> new values for all stat changes
- Aggregate related changes from multiple files into unified descriptions
- Note cross-cutting themes spanning multiple files
- Use ### subsection headers to organize within the theme
- If no changes are relevant, write only: `No changes.`
- Never use markdown tables
- Use inline bullets: `Item: old -> new detail | detail | detail`

Write output to: {{output_path}}"""


def prompt_dedup(section_a: str, section_b: str, path_a: str, path_b: str) -> str:
    return f"""Compare these two changelog sections and remove duplicate entries.

Section A: {section_a}
File: {path_a}

Section B: {section_b}
File: {path_b}

Rules:
- A "duplicate" is the same game change described in both sections, even if worded differently
- Keep the entry in whichever section is a better thematic fit
- Remove the duplicate from the other section — do not rewrite it, just delete that bullet
- If both are equally good fits, keep it in Section A (listed first)
- Do NOT remove entries that are merely related — only true duplicates describing the same change
- Do NOT rewrite, rephrase, or reorganize any content — only delete duplicate lines
- If no duplicates are found, respond with exactly: `No duplicates.`

If you removed duplicates, rewrite ONLY the modified section file(s) in full to their same path(s). Do not rewrite files that had no changes."""


# --- Core logic for each subcommand ---


def cmd_prepare(v1: str, v2: str) -> None:
    """Generate diffs, prepare batches, initialize progress."""
    pair_dir = DIFF_DIR / f"{v1}-{v2}"
    source_old = ROOT / "source" / v1
    source_new = ROOT / "source" / v2

    if not source_old.is_dir():
        sys.exit(f"Source not found: {source_old}")
    if not source_new.is_dir():
        sys.exit(f"Source not found: {source_new}")

    # Step 1: Generate diffs if needed
    if not pair_dir.exists() or not any(
        f for f in pair_dir.rglob("*.diff") if "_batches" not in f.parts
    ):
        print(f"Generating diffs {v1} -> {v2}...")
        result = subprocess.run(
            [sys.executable, str(ROOT / "diff-tools" / "version_diff.py"), v1, v2],
            cwd=ROOT, text=True, capture_output=True
        )
        if result.returncode != 0:
            sys.exit(f"Diff generation failed:\n{result.stderr}")
        print(result.stdout.strip())
    else:
        print(f"Diffs already exist at {pair_dir}")

    # Step 2: Prepare batches
    print(f"\nPreparing batches...")
    manifest = prepare_batches(v1, v2)
    total_kb = sum(e["size_kb"] for e in manifest)
    print(f"  {len(manifest)} batch files, {total_kb:.0f} KB total")

    # Step 3: Initialize progress
    progress = load_progress(v1, v2)
    if not progress:
        progress = {
            "v1": v1,
            "v2": v2,
            "phase": "analyze",
            "analyze": build_analyze_tasks(manifest),
            "summarize": [],
            "write": [],
        }
        save_progress(v1, v2, progress)
        print(f"\nReady. {len(progress['analyze'])} analyze tasks queued.")
    else:
        phase = progress["phase"]
        tasks = progress.get(phase, [])
        remaining = sum(1 for t in tasks if t["status"] == "pending")
        print(f"\nProgress exists. Phase: {phase}, {remaining} tasks remaining.")


def cmd_status(v1: str, v2: str) -> None:
    """Show current progress."""
    progress = load_progress(v1, v2)
    if not progress:
        print("No progress file. Run: pipeline.py prepare V1 V2")
        return

    phase = progress["phase"]
    tasks = progress.get(phase, [])
    done = sum(1 for t in tasks if t["status"] == "done")
    pending = sum(1 for t in tasks if t["status"] == "pending")

    print(f"Model: {LLM_MODEL}")
    print(f"Phase: {phase}")
    print(f"  Done: {done}/{len(tasks)}")
    print(f"  Remaining: {pending}")

    if pending > 0:
        next_task = next((t for t in tasks if t["status"] == "pending"), None)
        if next_task:
            print(f"  Next: {next_task['id']}")


def env_check_gate() -> None:
    """Print LLM_MODEL and wait, giving user a chance to interrupt."""
    label = f"LLM_MODEL = {LLM_MODEL}"
    inner = max(len(label) + 4, 54)
    print(f"╔{'═' * inner}╗")
    print(f"║  {label:<{inner - 2}}║")
    print(f"╚{'═' * inner}╝")
    print(f"Waiting 10s — interrupt (Ctrl+C) if model is wrong...")
    sys.stdout.flush()
    time.sleep(10)
    print()


def cmd_next(v1: str, v2: str) -> None:
    """Print the prompt and instructions for the next task."""
    progress = load_progress(v1, v2)
    if not progress:
        sys.exit("No progress file. Run: pipeline.py prepare V1 V2")

    if not progress.get("env_checked"):
        env_check_gate()
        progress["env_checked"] = True
        save_progress(v1, v2, progress)

    phase = progress["phase"]
    tasks = progress.get(phase, [])
    task = next((t for t in tasks if t["status"] == "pending"), None)

    if task is None:
        # Auto-advance phase
        if phase == "analyze":
            # Check if summarize is needed
            summarize_tasks = build_summarize_tasks(v1, v2)
            if summarize_tasks:
                progress["phase"] = "summarize"
                progress["summarize"] = summarize_tasks
                save_progress(v1, v2, progress)
                print(f"Analyze phase complete. Advanced to summarize ({len(summarize_tasks)} domains).")
                print("Run `next` again to get the first summarize task.")
            else:
                # Skip to write
                progress["phase"] = "write"
                progress["write"] = build_write_tasks(v1, v2)
                save_progress(v1, v2, progress)
                print(f"Analyze phase complete. No summarization needed. Advanced to write ({len(progress['write'])} sections).")
                print("Run `next` again to get the first write task.")
            return
        elif phase == "summarize":
            progress["phase"] = "write"
            progress["write"] = build_write_tasks(v1, v2)
            save_progress(v1, v2, progress)
            print(f"Summarize phase complete. Advanced to write ({len(progress['write'])} sections).")
            print("Run `next` again to get the first write task.")
            return
        elif phase == "write":
            dedup_tasks = build_dedup_tasks(v1, v2)
            if dedup_tasks:
                progress["phase"] = "dedup"
                progress["dedup"] = dedup_tasks
                save_progress(v1, v2, progress)
                print(f"Write phase complete. Advanced to dedup ({len(dedup_tasks)} pairs).")
                print("Run `next` again to get the first dedup task.")
            else:
                print("All phases complete. Run: pipeline.py assemble V1 V2")
            return
        elif phase == "dedup":
            print("All phases complete. Run: pipeline.py assemble V1 V2")
            return

    assert task is not None
    pair_dir = DIFF_DIR / f"{v1}-{v2}"
    rdir = run_dir(v1, v2)
    remaining = sum(1 for t in tasks if t["status"] == "pending")

    print(f"=== {phase.upper()} [{LLM_MODEL}] — {task['id']} ({remaining} remaining) ===\n")

    if phase == "analyze":
        batch_dir = pair_dir / "_batches"
        output_path = str(rdir / "_analysis" / f"{task['id']}.md")

        if task["type"] == "localization_mechanics":
            batch_paths = [str(batch_dir / f) for f in task["batch_files"]]
            index_files = task.get("index_batch_files", [])
            index_paths = [str(batch_dir / f) for f in index_files if (batch_dir / f).exists()] or None
            prompt = prompt_analyze_localization_mechanics(v1, v2, batch_paths, index_paths)
        elif task["type"] == "localization_lore":
            batch_paths = [str(batch_dir / f) for f in task["batch_files"]]
            prompt = prompt_analyze_localization_lore(v1, v2, batch_paths)
        else:
            batch_path = str(batch_dir / task["batch_file"])
            prompt = prompt_analyze_general(v1, v2, task["label"], batch_path)

        prompt = prompt.replace("{output_path}", output_path)
        print(prompt)
        print(f"\n--- Output: {output_path} ---")

    elif phase == "summarize":
        analysis_dir = rdir / "_analysis"
        files = [str(analysis_dir / f) for f in task["files"]]
        output_path = str(rdir / "_summary" / f"{task['id']}.md")
        prompt = prompt_summarize(v1, v2, task["id"], files)
        prompt = prompt.replace("{output_path}", output_path)
        print(prompt)
        print(f"\n--- Output: {output_path} ---")

    elif phase == "write":
        section = next(s for s in SECTIONS if s["id"] == task["id"])
        files = get_write_files(v1, v2, section)
        output_path = str(rdir / "_sections" / f"{task['id']}.md")
        prompt = prompt_write(v1, v2, section["label"], section["focus"], files)
        prompt = prompt.replace("{output_path}", output_path)
        print(prompt)
        print(f"\n--- Output: {output_path} ---")

    elif phase == "dedup":
        sections_dir = rdir / "_sections"
        path_a = str(sections_dir / f"{task['a']}.md")
        path_b = str(sections_dir / f"{task['b']}.md")
        label_a = next(s["label"] for s in SECTIONS if s["id"] == task["a"])
        label_b = next(s["label"] for s in SECTIONS if s["id"] == task["b"])
        prompt = prompt_dedup(label_a, label_b, path_a, path_b)
        print(prompt)
        print(f"\n--- Compare: {path_a} vs {path_b} ---")


def get_write_files(v1: str, v2: str, section: dict) -> list[str]:
    """Get eligible analysis/summary files for a write section."""
    rdir = run_dir(v1, v2)
    analysis_dir = rdir / "_analysis"
    summary_dir = rdir / "_summary"

    # Identify multi-part domains (use summary instead)
    multi_part = set()
    if analysis_dir.exists():
        for f in analysis_dir.glob("*.md"):
            m = re.match(r"^(.+?)--part\d+\.md$", f.name)
            if m:
                multi_part.add(m.group(1))

    eligible: dict[str, str] = {}

    # Multi-part domains: prefer summary, fall back to part files
    for domain in multi_part:
        summary_file = summary_dir / f"{domain}.md"
        if summary_file.exists():
            eligible[domain] = str(summary_file)
        elif analysis_dir.exists():
            parts = sorted(analysis_dir.glob(f"{domain}--part*.md"))
            if parts:
                print(f"WARNING: No summary for '{domain}' — using {len(parts)} part files", file=sys.stderr)
                for f in parts:
                    eligible[f.stem] = str(f)

    # Single-file domains use analysis directly
    if analysis_dir.exists():
        for f in sorted(analysis_dir.glob("*.md")):
            name = f.stem
            if not re.match(r"^.+--part\d+$", name) and name not in multi_part:
                eligible[name] = str(f)

    # Filter by section domains
    if "*" in section["domains"]:
        return sorted(eligible.values())

    result = []
    for name, path in eligible.items():
        for prefix in section["domains"]:
            if name == prefix or name.startswith(prefix + "--"):
                result.append(path)
                break
    return sorted(result)


def cmd_done(v1: str, v2: str) -> None:
    """Mark the current task as done."""
    progress = load_progress(v1, v2)
    if not progress:
        sys.exit("No progress file.")

    phase = progress["phase"]
    tasks = progress.get(phase, [])
    task = next((t for t in tasks if t["status"] == "pending"), None)

    if task is None:
        print("No pending task to mark done.")
        return

    # Verify output exists (dedup modifies existing files, no new output)
    rdir = run_dir(v1, v2)
    subdir = PHASE_SUBDIRS.get(phase)
    if subdir:
        output = rdir / subdir / f"{task['id']}.md"
        if not output.exists():
            sys.exit(f"Output not found: {output}\nWrite the file before marking done.")

    # Guard: check for orphan/premature files
    if not subdir:
        # dedup doesn't have its own output directory
        task["status"] = "done"
        save_progress(v1, v2, progress)
        remaining = sum(1 for t in tasks if t["status"] == "pending")
        print(f"Marked done: {task['id']}")
        print(f"Remaining in {phase}: {remaining}")
        if remaining == 0:
            print(f"\nPhase '{phase}' complete. Run `next` to advance.")
        return
    output_dir = rdir / subdir
    if output_dir.exists():
        valid_ids = {t["id"] for t in tasks}
        current_id = task["id"]
        for f in output_dir.iterdir():
            if not f.suffix == ".md":
                continue
            file_id = f.stem
            if file_id not in valid_ids:
                print(f"WARNING: Orphan file (no matching task): {f.name} — deleting")
                f.unlink()
            elif file_id != current_id and any(
                t["id"] == file_id and t["status"] == "pending" for t in tasks
            ):
                print(f"WARNING: File written for future task: {f.name} — deleting")
                f.unlink()

    task["status"] = "done"
    save_progress(v1, v2, progress)

    remaining = sum(1 for t in tasks if t["status"] == "pending")
    print(f"Marked done: {task['id']}")
    print(f"Remaining in {phase}: {remaining}")

    if remaining == 0:
        print(f"\nPhase '{phase}' complete. Run `next` to advance.")


def cmd_assemble(v1: str, v2: str) -> None:
    """Concatenate completed sections into final changelog."""
    rdir = run_dir(v1, v2)
    sections_dir = rdir / "_sections"

    if not sections_dir.exists():
        sys.exit(f"Sections directory not found: {sections_dir}")

    header = f"# X4 Foundations Changelog: {v1} \u2192 {v2}\n\n"
    parts = [header]
    included = []
    skipped = []

    for section in SECTIONS:
        section_file = sections_dir / f"{section['id']}.md"
        if not section_file.exists():
            skipped.append(section["id"])
            continue

        content = section_file.read_text().strip()
        if content == "No changes.":
            skipped.append(section["id"])
            continue

        parts.append(f"## {section['label']}\n\n{content}\n\n")
        included.append(section["id"])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"-{LLM_MODEL}" if LLM_MODEL else ""
    result_path = RESULTS_DIR / f"diff-{v1}-{v2}{suffix}.md"
    result_path.write_text("".join(parts))

    print(f"Assembled: {result_path}")
    print(f"  Included: {', '.join(included)}")
    if skipped:
        print(f"  Skipped (empty/missing): {', '.join(skipped)}")


def cmd_reset(v1: str, v2: str, phase: str | None) -> None:
    """Reset progress for a phase (or all phases)."""
    progress = load_progress(v1, v2)
    if not progress:
        print("No progress file to reset.")
        return

    if phase:
        if phase not in ("analyze", "summarize", "write", "dedup"):
            sys.exit(f"Unknown phase: {phase}")
        if phase == "analyze":
            manifest_path = DIFF_DIR / f"{v1}-{v2}" / "_batches" / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text())
                tasks = build_analyze_tasks(manifest)
            else:
                tasks = progress.get("analyze", [])
                for t in tasks:
                    t["status"] = "pending"
        elif phase == "summarize":
            tasks = build_summarize_tasks(v1, v2)
        elif phase == "write":
            tasks = build_write_tasks(v1, v2)
        elif phase == "dedup":
            tasks = build_dedup_tasks(v1, v2)
        progress[phase] = tasks
        progress["phase"] = phase
        save_progress(v1, v2, progress)
        print(f"Reset {phase} phase ({len(tasks)} tasks).")
    else:
        progress_path(v1, v2).unlink(missing_ok=True)
        print("Deleted progress file. Run `prepare` again.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="X4 diff pipeline orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Workflow:
  1. prepare V1 V2   — generate diffs, batch them, init progress
  2. next V1 V2      — get prompt for next task (feed to LLM)
  3. done V1 V2      — mark task complete after LLM writes output
  4. (repeat 2-3 until all phases complete)
  5. assemble V1 V2  — build final changelog from sections"""
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="Generate diffs, batch, init progress")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("status", help="Show current progress")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("next", help="Print prompt for next task")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("done", help="Mark current task complete")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("assemble", help="Build final changelog")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("reset", help="Reset progress")
    p.add_argument("v1")
    p.add_argument("v2")
    p.add_argument("phase", nargs="?", help="Phase to reset (or omit for full reset)")

    args = parser.parse_args()

    global LLM_MODEL
    LLM_MODEL = os.environ.get("LLM_MODEL", "")
    if not LLM_MODEL:
        sys.exit("LLM_MODEL must be set in .env or environment (e.g. LLM_MODEL=qwen3.5-27b)")

    if args.command == "prepare":
        cmd_prepare(args.v1, args.v2)
    elif args.command == "status":
        cmd_status(args.v1, args.v2)
    elif args.command == "next":
        cmd_next(args.v1, args.v2)
    elif args.command == "done":
        cmd_done(args.v1, args.v2)
    elif args.command == "assemble":
        cmd_assemble(args.v1, args.v2)
    elif args.command == "reset":
        cmd_reset(args.v1, args.v2, args.phase)


if __name__ == "__main__":
    main()
