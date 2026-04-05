#!/usr/bin/env python3
"""Get a batch of N pending analyze tasks at once, for parallel agent dispatch.

Usage:
    python3 diff-tools/task-batch.py analyze <V1> <V2> <N>

Returns JSON with batch of task objects (same shape as task-next.sh output).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    phase = sys.argv[1]
    v1 = sys.argv[2]
    v2 = sys.argv[3]
    n = int(sys.argv[4])

    if phase != "analyze":
        print(json.dumps({"error": f"Batch mode only supports analyze phase, got: {phase}"}))
        return

    from analyze_tasks import get_paths, get_task_info, read_manifest
    from task_common import read_progress

    paths = get_paths(v1, v2)

    if not paths["progress"].exists():
        print(json.dumps({"error": "No progress file. Run init first."}))
        return

    tasks = read_progress(paths["progress"])
    manifest = read_manifest(paths)

    pending_ids = [tid for checked, tid in tasks if not checked]
    batch = []

    for tid in pending_ids[:n]:
        info = get_task_info(tid, manifest, paths)
        if info is None:
            continue
        info["output"] = str(paths["analysis"] / f"{tid}.md")
        batch.append(info)

    remaining = len(pending_ids)
    result = {"batch": batch, "remaining": remaining}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
