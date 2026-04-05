#!/usr/bin/env python3
"""Manage analyze-phase tasks for diff analysis workflow.

Reads the batch manifest and progress file to provide task routing,
progress tracking, and status for the diff-analyze agent.

Subcommands:
    init V1 V2           Build progress file from manifest (idempotent)
    status V1 V2         Show progress: total, done, remaining, next task
    next V1 V2           Get next task info as JSON
    done V1 V2 TASK_ID   Mark a task complete

All output is JSON for easy parsing by agents.
"""

import argparse
import json
import sys

from task_common import (
    DIFF_DIR,
    check_existing_progress,
    mark_done,
    progress_status,
    read_progress,
    write_progress,
)


def get_paths(v1, v2):
    base = DIFF_DIR / f"{v1}-{v2}"
    return {
        "base": base,
        "analysis": base / "_analysis",
        "batches": base / "_analysis" / "_batches",
        "manifest": base / "_analysis" / "_batches" / "manifest.json",
        "progress": base / "_analysis" / "_progress.md",
    }


def read_manifest(paths):
    with open(paths["manifest"]) as f:
        return json.load(f)


def build_task_list(manifest, paths):
    """Build task list from manifest, handling localization special case."""
    tasks = []
    has_t = any(e["file"] == "t.diff" for e in manifest)

    for entry in manifest:
        task_id = entry["file"].replace(".diff", "")
        if task_id == "t":
            continue
        if task_id == "index" and has_t:
            continue
        tasks.append(task_id)

    if has_t:
        tasks.append("localization_mechanics")
        tasks.append("localization_lore")

    return tasks


def get_task_info(task_id, manifest, paths):
    """Get full info for a task: label, prompt_type, batch_files."""
    batches_dir = paths["batches"]

    if task_id == "localization_mechanics":
        files = [str(batches_dir / "t.diff")]
        if (batches_dir / "index.diff").exists():
            files.append(str(batches_dir / "index.diff"))
        return {
            "task_id": task_id,
            "label": "Localization text (English) — mechanics-related changes",
            "prompt_type": "localization_mechanics",
            "batch_files": files,
        }

    if task_id == "localization_lore":
        return {
            "task_id": task_id,
            "label": "Localization text (English) — story and lore changes",
            "prompt_type": "localization_lore",
            "batch_files": [str(batches_dir / "t.diff")],
        }

    diff_file = task_id + ".diff"
    for entry in manifest:
        if entry["file"] == diff_file:
            return {
                "task_id": task_id,
                "label": entry["label"],
                "prompt_type": "general",
                "batch_files": [str(batches_dir / diff_file)],
            }

    return None


# --- Subcommands ---


def cmd_init(args):
    paths = get_paths(args.v1, args.v2)

    existing = check_existing_progress(paths["progress"])
    if existing:
        print(json.dumps(existing))
        return

    if not paths["manifest"].exists():
        sys.exit(
            f"Manifest not found: {paths['manifest']}\n"
            "Run prepare_diff_analysis.py first."
        )

    manifest = read_manifest(paths)
    task_ids = build_task_list(manifest, paths)
    tasks = [(False, tid) for tid in task_ids]

    paths["progress"].parent.mkdir(parents=True, exist_ok=True)
    write_progress(paths["progress"], tasks)
    print(json.dumps({"status": "created", "total": len(tasks)}))


def cmd_status(args):
    paths = get_paths(args.v1, args.v2)
    if not paths["progress"].exists():
        print(json.dumps({"error": "No progress file. Run init first."}))
        return
    print(json.dumps(progress_status(read_progress(paths["progress"]), "next_task")))


def cmd_next(args):
    count = args.count or 1
    paths = get_paths(args.v1, args.v2)
    if not paths["progress"].exists():
        print(json.dumps({"error": "No progress file. Run init first."}))
        return

    tasks = read_progress(paths["progress"])
    manifest = read_manifest(paths)

    pending_ids = [tid for checked, tid in tasks if not checked]
    remaining = len(pending_ids)

    if not pending_ids:
        print(json.dumps({"done": True}))
        return

    if count == 1:
        info = get_task_info(pending_ids[0], manifest, paths)
        if info is None:
            print(json.dumps({"error": f"Task not found in manifest: {pending_ids[0]}"}))
            return
        info["remaining"] = remaining
        info["output"] = str(paths["analysis"] / f"{pending_ids[0]}.md")
        print(json.dumps(info))
    else:
        batch = []
        for tid in pending_ids[:count]:
            info = get_task_info(tid, manifest, paths)
            if info is None:
                continue
            info["output"] = str(paths["analysis"] / f"{tid}.md")
            batch.append(info)
        print(json.dumps({"batch": batch, "remaining": remaining}))


def cmd_done(args):
    paths = get_paths(args.v1, args.v2)
    print(json.dumps(mark_done(paths["progress"], args.task_id)))


def main():
    parser = argparse.ArgumentParser(description="Manage analyze-phase tasks")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Build progress file from manifest")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("status", help="Show progress")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("next", help="Get next task info as JSON")
    p.add_argument("v1")
    p.add_argument("v2")
    p.add_argument("--count", type=int, default=1, help="Number of tasks to return (default: 1)")

    p = sub.add_parser("done", help="Mark task complete")
    p.add_argument("v1")
    p.add_argument("v2")
    p.add_argument("task_id")

    args = parser.parse_args()
    cmds = {"init": cmd_init, "status": cmd_status, "next": cmd_next, "done": cmd_done}
    cmds[args.command](args)


if __name__ == "__main__":
    main()
