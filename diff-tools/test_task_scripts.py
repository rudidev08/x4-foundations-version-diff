#!/usr/bin/env python3
"""Test all task management scripts across three scenarios:
1. No data — missing prerequisites
2. Has data — partial progress
3. Done — all tasks complete

Creates temporary fake files in diff/_test-OLD-NEW/, runs all subcommands,
verifies outputs, and cleans up.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIFF_DIR = ROOT / "diff"
RESULTS_DIR = ROOT / "diff-results"
V1, V2 = "_test-OLD", "_test-NEW"
PAIR = f"{V1}-{V2}"
BASE = DIFF_DIR / PAIR

passed = 0
failed = 0


def run(script, *args):
    """Run a script, return (exit_code, stdout, stderr)."""
    cmd = ["python3", f"diff-tools/{script}", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def parse_json(stdout):
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS  {name}")
        passed += 1
    else:
        print(f"  FAIL  {name}  {detail}")
        failed += 1


def cleanup():
    if BASE.exists():
        shutil.rmtree(BASE)
    result_file = RESULTS_DIR / f"diff-{V1}-{V2}.md"
    if result_file.exists():
        result_file.unlink()


def create_fake_manifest():
    """Create a minimal manifest with 3 general tasks + t.diff + index.diff for localization."""
    batches = BASE / "_analysis" / "_batches"
    batches.mkdir(parents=True, exist_ok=True)

    manifest = [
        {"file": "libraries--part1.diff", "domain": "libraries", "label": "Game data libraries", "part": 1, "total_parts": 2, "diff_count": 3, "size_kb": 10.0},
        {"file": "libraries--part2.diff", "domain": "libraries", "label": "Game data libraries", "part": 2, "total_parts": 2, "diff_count": 2, "size_kb": 8.0},
        {"file": "aiscripts.diff", "domain": "aiscripts", "label": "AI behavior scripts", "part": None, "total_parts": None, "diff_count": 5, "size_kb": 15.0},
        {"file": "t.diff", "domain": "t", "label": "Localization text (English)", "part": None, "total_parts": None, "diff_count": 1, "size_kb": 50.0},
        {"file": "index.diff", "domain": "index", "label": "Master lookup tables", "part": None, "total_parts": None, "diff_count": 1, "size_kb": 2.0},
    ]

    (batches / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # Create fake batch diff files
    for entry in manifest:
        (batches / entry["file"]).write_text(f"fake diff content for {entry['file']}\n")


def create_fake_analysis_results():
    """Create fake analysis .md result files (as if analyze phase completed)."""
    analysis = BASE / "_analysis"
    analysis.mkdir(parents=True, exist_ok=True)

    # Multi-part domain
    (analysis / "libraries--part1.md").write_text("### Libraries — Critical\n- Ware X: 100 → 200\n")
    (analysis / "libraries--part2.md").write_text("### Libraries — Medium\n- Job Y tweaked\n")

    # Single-file domains
    (analysis / "aiscripts.md").write_text("### AI Scripts — Critical\n- New flee behavior\n")
    (analysis / "localization_mechanics.md").write_text("### Localization Mechanics — High\n- New UI string\n")
    (analysis / "localization_lore.md").write_text("### Story & Lore — New\n- New character\n")


def create_fake_summary():
    """Create fake summary file for multi-part domain."""
    summary = BASE / "_summary"
    summary.mkdir(parents=True, exist_ok=True)
    (summary / "libraries.md").write_text("### Libraries — Consolidated\n- All lib changes\n")


def create_fake_sections():
    """Create fake section files as if write phase completed."""
    sections = BASE / "_sections"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "combat.md").write_text("### Weapons\n- Laser damage: 100 → 150\n")
    (sections / "new_mechanics.md").write_text("No changes.")
    (sections / "economy_trade.md").write_text("### Trade\n- Ore price: 50 → 75\n")
    (sections / "missions.md").write_text("No changes.")
    (sections / "ui.md").write_text("### HUD\n- New minimap\n")
    (sections / "ship_balance.md").write_text("### Fighters\n- Hull: 1000 → 1200\n")
    (sections / "dlc.md").write_text("No changes.")
    (sections / "new_content.md").write_text("### New Ships\n- Argon Destroyer\n")
    (sections / "bug_fixes.md").write_text("### Fixes\n- Fixed crash\n")
    (sections / "miscellaneous.md").write_text("No changes.")


# =============================================================================
# Test: analyze_tasks.py
# =============================================================================

def test_analyze_no_data():
    print("\n--- analyze_tasks.py: NO DATA ---")
    cleanup()
    BASE.mkdir(parents=True, exist_ok=True)

    code, out, err = run("analyze_tasks.py", "init", V1, V2)
    check("init: exits non-zero without manifest", code != 0)
    check("init: error mentions manifest", "Manifest not found" in err or "manifest" in err.lower())

    code, out, err = run("analyze_tasks.py", "status", V1, V2)
    j = parse_json(out)
    check("status: returns error JSON", j is not None and "error" in j)

    code, out, err = run("analyze_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: returns error JSON", j is not None and "error" in j)


def test_analyze_has_data():
    print("\n--- analyze_tasks.py: HAS DATA ---")
    cleanup()
    create_fake_manifest()

    # Init
    code, out, err = run("analyze_tasks.py", "init", V1, V2)
    j = parse_json(out)
    check("init: success", j is not None and j.get("status") == "created")
    check("init: correct task count", j is not None and j.get("total") == 5,
          f"expected 5, got {j.get('total') if j else 'None'}")

    # Re-init (idempotent)
    code, out, err = run("analyze_tasks.py", "init", V1, V2)
    j = parse_json(out)
    check("init: idempotent", j is not None and j.get("status") == "exists")

    # Status
    code, out, err = run("analyze_tasks.py", "status", V1, V2)
    j = parse_json(out)
    check("status: all remaining", j is not None and j.get("remaining") == j.get("total"))

    # Next
    code, out, err = run("analyze_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: returns first task", j is not None and j.get("task_id") == "libraries--part1")
    check("next: has batch_files", j is not None and len(j.get("batch_files", [])) > 0)
    check("next: has prompt_type", j is not None and j.get("prompt_type") == "general")
    check("next: has output path", j is not None and "output" in j)

    # Done
    code, out, err = run("analyze_tasks.py", "done", V1, V2, "libraries--part1")
    j = parse_json(out)
    check("done: marks task", j is not None and j.get("marked") == "libraries--part1")

    # Next should advance
    code, out, err = run("analyze_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: advances after done", j is not None and j.get("task_id") == "libraries--part2")

    # Status after one done
    code, out, err = run("analyze_tasks.py", "status", V1, V2)
    j = parse_json(out)
    check("status: done=1", j is not None and j.get("done") == 1)

    # Done invalid task
    code, out, err = run("analyze_tasks.py", "done", V1, V2, "nonexistent")
    j = parse_json(out)
    check("done: error for invalid task", j is not None and "error" in j)


def test_analyze_localization():
    """Verify localization special case routing."""
    print("\n--- analyze_tasks.py: LOCALIZATION ROUTING ---")
    # Continue from has_data state — mark remaining general tasks done
    for tid in ["libraries--part2", "aiscripts"]:
        run("analyze_tasks.py", "done", V1, V2, tid)

    # Next should be localization_mechanics
    code, out, err = run("analyze_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("loc_mechanics: correct task", j is not None and j.get("task_id") == "localization_mechanics")
    check("loc_mechanics: correct prompt_type", j is not None and j.get("prompt_type") == "localization_mechanics")
    check("loc_mechanics: reads t.diff + index.diff", j is not None and len(j.get("batch_files", [])) == 2)

    run("analyze_tasks.py", "done", V1, V2, "localization_mechanics")

    # Next should be localization_lore
    code, out, err = run("analyze_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("loc_lore: correct task", j is not None and j.get("task_id") == "localization_lore")
    check("loc_lore: correct prompt_type", j is not None and j.get("prompt_type") == "localization_lore")
    check("loc_lore: reads only t.diff", j is not None and len(j.get("batch_files", [])) == 1)


def test_analyze_done():
    print("\n--- analyze_tasks.py: ALL DONE ---")
    run("analyze_tasks.py", "done", V1, V2, "localization_lore")

    code, out, err = run("analyze_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: returns done=true", j is not None and j.get("done") is True)

    code, out, err = run("analyze_tasks.py", "status", V1, V2)
    j = parse_json(out)
    check("status: remaining=0", j is not None and j.get("remaining") == 0)


# =============================================================================
# Test: summarize_tasks.py
# =============================================================================

def test_summarize_no_data():
    print("\n--- summarize_tasks.py: NO DATA ---")
    cleanup()
    BASE.mkdir(parents=True, exist_ok=True)

    code, out, err = run("summarize_tasks.py", "init", V1, V2)
    check("init: exits non-zero without analysis", code != 0)

    code, out, err = run("summarize_tasks.py", "status", V1, V2)
    j = parse_json(out)
    check("status: returns error JSON", j is not None and "error" in j)


def test_summarize_no_multipart():
    """Only single-file results — nothing to summarize."""
    print("\n--- summarize_tasks.py: NO MULTI-PART ---")
    cleanup()
    analysis = BASE / "_analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "aiscripts.md").write_text("single file\n")
    (analysis / "localization_mechanics.md").write_text("single file\n")

    code, out, err = run("summarize_tasks.py", "init", V1, V2)
    j = parse_json(out)
    check("init: nothing to summarize", j is not None and j.get("status") == "nothing_to_summarize")


def test_summarize_has_data():
    print("\n--- summarize_tasks.py: HAS DATA ---")
    cleanup()
    analysis = BASE / "_analysis"
    analysis.mkdir(parents=True, exist_ok=True)

    # Multi-part domain: libraries (3 parts — single-pass)
    for i in range(1, 4):
        (analysis / f"libraries--part{i}.md").write_text(f"libraries part {i}\n")

    # Multi-part domain: md (10 parts — hierarchical)
    for i in range(1, 11):
        (analysis / f"md--part{i}.md").write_text(f"md part {i}\n")

    # Single-file domain (should be ignored)
    (analysis / "aiscripts.md").write_text("single file\n")

    # Init
    code, out, err = run("summarize_tasks.py", "init", V1, V2)
    j = parse_json(out)
    check("init: success", j is not None and j.get("status") == "created")
    check("init: found 2 domains", j is not None and j.get("total") == 2)

    domains = {d["domain"]: d for d in j.get("domains", [])}
    check("init: md is hierarchical", domains.get("md", {}).get("type") == "hierarchical")
    check("init: libraries is single-pass", domains.get("libraries", {}).get("type") == "single-pass")
    check("init: sorted by file count desc", j["domains"][0]["domain"] == "md")

    # Idempotent
    code, out, err = run("summarize_tasks.py", "init", V1, V2)
    j = parse_json(out)
    check("init: idempotent", j is not None and j.get("status") == "exists")

    # Status
    code, out, err = run("summarize_tasks.py", "status", V1, V2)
    j = parse_json(out)
    check("status: 2 remaining", j is not None and j.get("remaining") == 2)

    # Next — should be md (largest first)
    code, out, err = run("summarize_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: returns md first", j is not None and j.get("domain") == "md")
    check("next: hierarchical type", j is not None and j.get("type") == "hierarchical")
    check("next: 10 files", j is not None and j.get("file_count") == 10)
    check("next: has chunks", j is not None and "chunks" in j)
    check("next: 2 chunks (10 files / 8)", j is not None and j.get("total_chunks") == 2)
    check("next: chunk 1 has 8 files", j is not None and len(j["chunks"][0]["files"]) == 8)
    check("next: chunk 2 has 2 files", j is not None and len(j["chunks"][1]["files"]) == 2)
    check("next: has output path", j is not None and "output" in j)

    # Done
    code, out, err = run("summarize_tasks.py", "done", V1, V2, "md")
    j = parse_json(out)
    check("done: marks md", j is not None and j.get("marked") == "md")
    check("done: 1 remaining", j is not None and j.get("remaining") == 1)

    # Next — should be libraries now
    code, out, err = run("summarize_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: advances to libraries", j is not None and j.get("domain") == "libraries")
    check("next: single-pass type", j is not None and j.get("type") == "single-pass")
    check("next: no chunks field", j is not None and "chunks" not in j)

    # Done invalid
    code, out, err = run("summarize_tasks.py", "done", V1, V2, "nonexistent")
    j = parse_json(out)
    check("done: error for invalid", j is not None and "error" in j)


def test_summarize_done():
    print("\n--- summarize_tasks.py: ALL DONE ---")
    run("summarize_tasks.py", "done", V1, V2, "libraries")

    code, out, err = run("summarize_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: returns done=true", j is not None and j.get("done") is True)

    # Cleanup
    # Create fake intermediate to verify cleanup removes it
    intermediate = BASE / "_summary" / "_intermediate"
    intermediate.mkdir(parents=True, exist_ok=True)
    (intermediate / "md--chunk1.md").write_text("chunk\n")

    code, out, err = run("summarize_tasks.py", "cleanup", V1, V2)
    j = parse_json(out)
    check("cleanup: removes files", j is not None and len(j.get("removed", [])) == 2)
    check("cleanup: intermediate gone", not intermediate.exists())
    check("cleanup: progress gone", not (BASE / "_summary" / "_progress.md").exists())


# =============================================================================
# Test: write_tasks.py
# =============================================================================

def test_write_no_data():
    print("\n--- write_tasks.py: NO DATA ---")
    cleanup()
    BASE.mkdir(parents=True, exist_ok=True)

    code, out, err = run("write_tasks.py", "init", V1, V2)
    check("init: exits non-zero without analyze flag", code != 0)
    check("init: error mentions analyze", "analyze" in err.lower())


def test_write_missing_summary():
    """Multi-part domains exist but no summary — should fail."""
    print("\n--- write_tasks.py: MISSING SUMMARY ---")
    cleanup()
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / "_completed_analyze").touch()
    create_fake_analysis_results()  # Has libraries--part1/part2 but no summary

    code, out, err = run("write_tasks.py", "init", V1, V2)
    check("init: exits non-zero without summaries", code != 0)
    check("init: error mentions summarize", "summarize" in err.lower())


def test_write_has_data():
    print("\n--- write_tasks.py: HAS DATA ---")
    cleanup()
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / "_completed_analyze").touch()
    create_fake_analysis_results()
    create_fake_summary()

    # Init
    code, out, err = run("write_tasks.py", "init", V1, V2)
    j = parse_json(out)
    check("init: success", j is not None and j.get("status") == "created")
    check("init: 10 sections", j is not None and j.get("total") == 10)

    # Idempotent
    code, out, err = run("write_tasks.py", "init", V1, V2)
    j = parse_json(out)
    check("init: idempotent", j is not None and j.get("status") == "exists")

    # Status
    code, out, err = run("write_tasks.py", "status", V1, V2)
    j = parse_json(out)
    check("status: 10 remaining", j is not None and j.get("remaining") == 10)

    # Next — should be combat
    code, out, err = run("write_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: returns combat first", j is not None and j.get("section_id") == "combat")
    check("next: has focus", j is not None and "focus" in j)
    check("next: has files", j is not None and len(j.get("files", [])) > 0)
    check("next: files use summary for multi-part",
          j is not None and any("_summary" in f for f in j.get("files", [])))
    check("next: files use analysis for single-file",
          j is not None and any("_analysis" in f and "part" not in f for f in j.get("files", [])))
    check("next: has output path", j is not None and "output" in j)

    # Done
    code, out, err = run("write_tasks.py", "done", V1, V2, "combat")
    j = parse_json(out)
    check("done: marks combat", j is not None and j.get("marked") == "combat")
    check("done: 9 remaining", j is not None and j.get("remaining") == 9)

    # Next advances
    code, out, err = run("write_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: advances to new_mechanics", j is not None and j.get("section_id") == "new_mechanics")

    # Verify wildcard sections get all files
    for tid in ["new_mechanics", "economy_trade", "missions", "ui", "ship_balance", "dlc"]:
        run("write_tasks.py", "done", V1, V2, tid)

    code, out, err = run("write_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: new_content gets all files (wildcard)",
          j is not None and j.get("section_id") == "new_content" and len(j.get("files", [])) >= 4)

    # Done invalid
    code, out, err = run("write_tasks.py", "done", V1, V2, "nonexistent")
    j = parse_json(out)
    check("done: error for invalid", j is not None and "error" in j)


def test_write_done():
    print("\n--- write_tasks.py: ALL DONE + ASSEMBLE + CLEANUP ---")
    for tid in ["new_content", "bug_fixes", "miscellaneous"]:
        run("write_tasks.py", "done", V1, V2, tid)

    code, out, err = run("write_tasks.py", "next", V1, V2)
    j = parse_json(out)
    check("next: returns done=true", j is not None and j.get("done") is True)

    # Create fake sections for assemble
    create_fake_sections()

    code, out, err = run("write_tasks.py", "assemble", V1, V2)
    j = parse_json(out)
    check("assemble: success", j is not None and "output" in j)
    check("assemble: skips 'No changes' sections",
          j is not None and "new_mechanics" in j.get("skipped", []))
    check("assemble: includes content sections",
          j is not None and "combat" in j.get("included", []))

    result_file = Path(j["output"]) if j else None
    if result_file and result_file.exists():
        content = result_file.read_text()
        check("assemble: has header", f"# X4 Foundations Changelog: {V1} → {V2}" in content)
        check("assemble: has combat section", "## Combat System" in content)
        check("assemble: excludes empty sections", "## New Game Systems" not in content)
    else:
        check("assemble: output file exists", False, "file not found")

    # Cleanup
    code, out, err = run("write_tasks.py", "cleanup", V1, V2)
    j = parse_json(out)
    check("cleanup: removes files", j is not None and len(j.get("removed", [])) == 2)
    check("cleanup: sections gone", not (BASE / "_sections").exists())
    check("cleanup: progress gone", not (BASE / "_write_progress.md").exists())


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    try:
        test_analyze_no_data()
        test_analyze_has_data()
        test_analyze_localization()
        test_analyze_done()

        test_summarize_no_data()
        test_summarize_no_multipart()
        test_summarize_has_data()
        test_summarize_done()

        test_write_no_data()
        test_write_missing_summary()
        test_write_has_data()
        test_write_done()
    finally:
        cleanup()

    print(f"\n{'=' * 40}")
    print(f"  {passed} passed, {failed} failed")
    print(f"{'=' * 40}")
    sys.exit(1 if failed else 0)
