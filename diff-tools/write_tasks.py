#!/usr/bin/env python3
"""Manage write-phase tasks for diff analysis workflow.

Routes analysis/summary result files to thematic sections, manages
progress tracking, and assembles the final changelog.

Subcommands:
    init V1 V2            Create progress file, verify prerequisites
    status V1 V2          Show progress
    next V1 V2            Get next section info as JSON (includes filtered file list)
    done V1 V2 SECTION    Mark section complete
    assemble V1 V2        Concatenate completed sections into final changelog
    cleanup V1 V2         Delete progress file and sections directory

All output is JSON for easy parsing by agents.
"""

import argparse
import json
import re
import shutil
import sys

from task_common import (
    DIFF_DIR,
    ROOT,
    check_existing_progress,
    mark_done,
    progress_status,
    read_progress,
)

RESULTS_DIR = ROOT / "diff-results"

SECTIONS = [
    {
        "id": "combat",
        "label": "Combat System",
        "focus": "Shields, weapons, missiles, turrets, AI targeting, weapon heat, disruption mechanics",
        "domains": ["libraries", "aiscripts", "assets--props", "assets--units", "md", "extensions"],
    },
    {
        "id": "new_mechanics",
        "label": "New Game Systems",
        "focus": "New attributes, new gameplay features, new AI behaviors",
        "domains": ["libraries", "aiscripts", "md", "maps", "localization_mechanics", "extensions"],
    },
    {
        "id": "economy_trade",
        "label": "Economy & Trade",
        "focus": "Ware pricing, production recipes, trade AI, resource flow",
        "domains": ["libraries", "aiscripts", "md", "assets--structures", "extensions"],
    },
    {
        "id": "missions",
        "label": "Mission System",
        "focus": "Mission logic, subscriptions, rewards, faction goals",
        "domains": ["md", "localization_mechanics", "extensions"],
    },
    {
        "id": "ui",
        "label": "UI & Interface",
        "focus": "Menus, HUD, panels, Lua scripts, notifications",
        "domains": ["ui", "localization_mechanics"],
    },
    {
        "id": "ship_balance",
        "label": "Ship Balance",
        "focus": "Hull, mass, thrust, inertia, drag, crew, storage, engine stats, physics",
        "domains": ["libraries", "assets--units", "assets--props", "extensions"],
    },
    {
        "id": "dlc",
        "label": "DLC-Specific",
        "focus": "Content unique to specific DLCs that doesn't fit other sections",
        "domains": ["extensions"],
    },
    {
        "id": "new_content",
        "label": "New Content",
        "focus": "New ships, wares, stations, story, characters, missions",
        "domains": ["*"],
    },
    {
        "id": "bug_fixes",
        "label": "Bug Fixes",
        "focus": "Corrected values, fixed logic, resolved issues",
        "domains": ["*"],
    },
    {
        "id": "miscellaneous",
        "label": "Miscellaneous",
        "focus": "Anything not covered by other sections",
        "domains": ["*"],
    },
]


def get_paths(v1, v2):
    base = DIFF_DIR / f"{v1}-{v2}"
    return {
        "base": base,
        "analysis": base / "_analysis",
        "summary": base / "_summary",
        "sections": base / "_sections",
        "progress": base / "_write_progress.md",
        "result": RESULTS_DIR / f"diff-{v1}-{v2}.md",
    }


def get_eligible_files(paths):
    """Determine which result files to use for each domain.

    Multi-part domains use consolidated summaries from _summary/.
    Single-file domains use _analysis/ files directly.
    """
    analysis = paths["analysis"]
    summary = paths["summary"]

    multi_part_domains = set()

    for f in sorted(analysis.glob("*.md")):
        if f.name == "_progress.md":
            continue
        m = re.match(r"^(.+?)--part\d+\.md$", f.name)
        if m:
            multi_part_domains.add(m.group(1))

    eligible = {}
    missing_summaries = []

    for domain in multi_part_domains:
        summary_file = summary / f"{domain}.md"
        if summary_file.exists():
            eligible[domain] = str(summary_file)
        else:
            missing_summaries.append(domain)

    for f in sorted(analysis.glob("*.md")):
        if f.name == "_progress.md":
            continue
        name = f.stem
        if not re.match(r"^.+--part\d+$", name) and name not in multi_part_domains:
            eligible[name] = str(f)

    return eligible, missing_summaries


def filter_files_for_section(eligible, section):
    if "*" in section["domains"]:
        return sorted(eligible.values())

    result = []
    for name, path in eligible.items():
        for prefix in section["domains"]:
            if name == prefix or name.startswith(prefix + "--"):
                result.append(path)
                break
    return sorted(result)


# --- Subcommands ---


def cmd_init(args):
    paths = get_paths(args.v1, args.v2)

    existing = check_existing_progress(paths["progress"])
    if existing:
        print(json.dumps(existing))
        return

    if not (paths["base"] / "_completed_analyze").exists():
        sys.exit("Analysis not complete. Run /diff-analyze first.")

    eligible, missing = get_eligible_files(paths)
    if missing:
        sys.exit(
            f"Missing summaries for multi-part domains: {', '.join(missing)}\n"
            "Run /diff-summarize first."
        )

    paths["sections"].mkdir(parents=True, exist_ok=True)
    lines = [f"- [ ] {s['id']}\n" for s in SECTIONS]
    paths["progress"].write_text("".join(lines))

    print(json.dumps({"status": "created", "total": len(SECTIONS)}))


def cmd_status(args):
    paths = get_paths(args.v1, args.v2)
    if not paths["progress"].exists():
        print(json.dumps({"error": "No progress file. Run init first."}))
        return
    print(json.dumps(progress_status(read_progress(paths["progress"]), "next_section")))


def cmd_next(args):
    paths = get_paths(args.v1, args.v2)
    if not paths["progress"].exists():
        print(json.dumps({"error": "No progress file. Run init first."}))
        return

    tasks = read_progress(paths["progress"])
    next_id = next((tid for checked, tid in tasks if not checked), None)

    if next_id is None:
        print(json.dumps({"done": True}))
        return

    section = next((s for s in SECTIONS if s["id"] == next_id), None)
    if section is None:
        print(json.dumps({"error": f"Unknown section: {next_id}"}))
        return

    eligible, _ = get_eligible_files(paths)
    files = filter_files_for_section(eligible, section)

    print(
        json.dumps(
            {
                "section_id": section["id"],
                "label": section["label"],
                "focus": section["focus"],
                "files": files,
                "remaining": sum(1 for checked, _ in tasks if not checked),
                "output": str(paths["sections"] / f"{section['id']}.md"),
            }
        )
    )


def cmd_done(args):
    paths = get_paths(args.v1, args.v2)
    print(json.dumps(mark_done(paths["progress"], args.section_id)))


def cmd_assemble(args):
    paths = get_paths(args.v1, args.v2)

    header = f"# X4 Foundations Changelog: {args.v1} \u2192 {args.v2}\n\n"
    parts = [header]
    included = []
    skipped = []

    for section in SECTIONS:
        section_file = paths["sections"] / f"{section['id']}.md"
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
    paths["result"].write_text("".join(parts))

    print(
        json.dumps(
            {
                "output": str(paths["result"]),
                "included": included,
                "skipped": skipped,
            }
        )
    )


def cmd_cleanup(args):
    paths = get_paths(args.v1, args.v2)
    removed = []

    if paths["sections"].exists():
        shutil.rmtree(paths["sections"])
        removed.append(str(paths["sections"]))

    if paths["progress"].exists():
        paths["progress"].unlink()
        removed.append(str(paths["progress"]))

    print(json.dumps({"removed": removed}))


def main():
    parser = argparse.ArgumentParser(description="Manage write-phase tasks")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create progress file, verify prerequisites")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("status", help="Show progress")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("next", help="Get next section info as JSON")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("done", help="Mark section complete")
    p.add_argument("v1")
    p.add_argument("v2")
    p.add_argument("section_id")

    p = sub.add_parser("assemble", help="Concatenate sections into final changelog")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("cleanup", help="Delete progress file and sections directory")
    p.add_argument("v1")
    p.add_argument("v2")

    args = parser.parse_args()
    cmds = {
        "init": cmd_init,
        "status": cmd_status,
        "next": cmd_next,
        "done": cmd_done,
        "assemble": cmd_assemble,
        "cleanup": cmd_cleanup,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
