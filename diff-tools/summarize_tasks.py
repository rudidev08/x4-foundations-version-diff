#!/usr/bin/env python3
"""Manage summarize-phase tasks for diff analysis workflow.

Discovers multi-part analysis results, groups them by domain, classifies
as hierarchical or single-pass, and provides chunking info for agents.

Subcommands:
    init V1 V2           Discover groups, create progress file (idempotent)
    status V1 V2         Show progress
    next V1 V2           Get next domain info as JSON (includes file lists and chunks)
    done V1 V2 DOMAIN    Mark domain complete
    cleanup V1 V2        Delete intermediate files and progress tracker

All output is JSON for easy parsing by agents.
"""

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict

from task_common import (
    DIFF_DIR,
    check_existing_progress,
    mark_done,
    progress_status,
    read_progress,
    write_progress,
)

CHUNK_SIZE = 8


def get_paths(v1, v2):
    base = DIFF_DIR / f"{v1}-{v2}"
    return {
        "base": base,
        "analysis": base / "_analysis",
        "summary": base / "_summary",
        "intermediate": base / "_summary" / "_intermediate",
        "progress": base / "_summary" / "_progress.md",
    }


def discover_groups(analysis_dir):
    """Find multi-part domains and group by domain root.

    Only returns groups with 2+ files. Skips _progress.md.
    """
    groups = defaultdict(list)

    for f in sorted(analysis_dir.glob("*.md")):
        if f.name == "_progress.md":
            continue

        name = f.stem
        m = re.match(r"^(.+?)--part(\d+)$", name)
        if m:
            domain_root = m.group(1)
            part_num = int(m.group(2))
            groups[domain_root].append((part_num, f))

    result = {}
    for domain, files in groups.items():
        if len(files) >= 2:
            files.sort(key=lambda x: x[0])
            result[domain] = [f for _, f in files]

    return result


def classify_domain(file_count):
    return "hierarchical" if file_count > CHUNK_SIZE else "single-pass"


def chunk_files(files, chunk_size=CHUNK_SIZE):
    return [files[i : i + chunk_size] for i in range(0, len(files), chunk_size)]


# --- Subcommands ---


def cmd_init(args):
    paths = get_paths(args.v1, args.v2)

    existing = check_existing_progress(paths["progress"])
    if existing:
        print(json.dumps(existing))
        return

    if not paths["analysis"].exists():
        sys.exit(f"Analysis directory not found: {paths['analysis']}\nRun /diff-analyze first.")

    groups = discover_groups(paths["analysis"])

    if not groups:
        print(json.dumps({"status": "nothing_to_summarize", "total": 0}))
        return

    sorted_domains = sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)

    tasks = []
    summary = []
    for domain, files in sorted_domains:
        dtype = classify_domain(len(files))
        tasks.append((False, domain))
        summary.append({"domain": domain, "file_count": len(files), "type": dtype})

    paths["progress"].parent.mkdir(parents=True, exist_ok=True)
    write_progress(paths["progress"], tasks)

    print(json.dumps({"status": "created", "total": len(sorted_domains), "domains": summary}))


def cmd_status(args):
    paths = get_paths(args.v1, args.v2)
    if not paths["progress"].exists():
        print(json.dumps({"error": "No progress file. Run init first."}))
        return
    print(json.dumps(progress_status(read_progress(paths["progress"]), "next_domain")))


def _build_domain_info(domain, groups, paths):
    """Build full info dict for a single domain."""
    files = groups.get(domain, [])
    file_paths = [str(f) for f in files]
    dtype = classify_domain(len(files))

    result = {
        "domain": domain,
        "type": dtype,
        "file_count": len(files),
        "files": file_paths,
        "output": str(paths["summary"] / f"{domain}.md"),
    }

    if dtype == "hierarchical":
        chunks = chunk_files(file_paths)
        result["chunks"] = [
            {
                "index": i + 1,
                "files": chunk,
                "intermediate_output": str(
                    paths["intermediate"] / f"{domain}--chunk{i + 1}.md"
                ),
            }
            for i, chunk in enumerate(chunks)
        ]
        result["total_chunks"] = len(chunks)

    return result


def cmd_next(args):
    count = args.count or 1
    paths = get_paths(args.v1, args.v2)
    if not paths["progress"].exists():
        print(json.dumps({"error": "No progress file. Run init first."}))
        return

    tasks = read_progress(paths["progress"])
    pending_ids = [tid for checked, tid in tasks if not checked]
    remaining = len(pending_ids)

    if not pending_ids:
        print(json.dumps({"done": True}))
        return

    groups = discover_groups(paths["analysis"])

    if count == 1:
        result = _build_domain_info(pending_ids[0], groups, paths)
        result["remaining"] = remaining
        print(json.dumps(result))
    else:
        batch = [_build_domain_info(d, groups, paths) for d in pending_ids[:count]]
        print(json.dumps({"batch": batch, "remaining": remaining}))


def cmd_done(args):
    paths = get_paths(args.v1, args.v2)
    print(json.dumps(mark_done(paths["progress"], args.domain)))


def cmd_cleanup(args):
    paths = get_paths(args.v1, args.v2)
    removed = []

    if paths["intermediate"].exists():
        shutil.rmtree(paths["intermediate"])
        removed.append(str(paths["intermediate"]))

    if paths["progress"].exists():
        paths["progress"].unlink()
        removed.append(str(paths["progress"]))

    print(json.dumps({"removed": removed}))


def main():
    parser = argparse.ArgumentParser(description="Manage summarize-phase tasks")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Discover groups, create progress file")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("status", help="Show progress")
    p.add_argument("v1")
    p.add_argument("v2")

    p = sub.add_parser("next", help="Get next domain info as JSON")
    p.add_argument("v1")
    p.add_argument("v2")
    p.add_argument("--count", type=int, default=1, help="Number of domains to return (default: 1)")

    p = sub.add_parser("done", help="Mark domain complete")
    p.add_argument("v1")
    p.add_argument("v2")
    p.add_argument("domain")

    p = sub.add_parser("cleanup", help="Delete intermediate files and progress")
    p.add_argument("v1")
    p.add_argument("v2")

    args = parser.parse_args()
    cmds = {
        "init": cmd_init,
        "status": cmd_status,
        "next": cmd_next,
        "done": cmd_done,
        "cleanup": cmd_cleanup,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
