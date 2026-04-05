"""Shared utilities for diff analysis task management scripts."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIFF_DIR = ROOT / "diff"


def read_progress(path):
    """Parse checkbox-format progress file into (checked, task_id) tuples."""
    tasks = []
    with open(path) as f:
        for line in f:
            m = re.match(r"- \[([ x])\] (\S+)", line.strip())
            if m:
                tasks.append((m.group(1) == "x", m.group(2)))
    return tasks


def write_progress(path, tasks):
    """Write simple checkbox progress file (no annotations)."""
    lines = [f"- [{'x' if done else ' '}] {tid}\n" for done, tid in tasks]
    path.write_text("".join(lines))


def progress_status(tasks, next_key="next_task"):
    """Compute status dict from parsed task list."""
    done = sum(1 for checked, _ in tasks if checked)
    remaining = len(tasks) - done
    next_id = next((tid for checked, tid in tasks if not checked), None)
    return {"total": len(tasks), "done": done, "remaining": remaining, next_key: next_id}


def check_existing_progress(progress_path):
    """If progress file exists, return status dict. Otherwise return None."""
    if progress_path.exists():
        tasks = read_progress(progress_path)
        done = sum(1 for checked, _ in tasks if checked)
        return {"status": "exists", "total": len(tasks), "done": done}
    return None


def mark_done(progress_path, task_id):
    """Mark a task complete in a simple (no-annotation) progress file."""
    tasks = read_progress(progress_path)
    for i, (checked, tid) in enumerate(tasks):
        if tid == task_id:
            tasks[i] = (True, tid)
            write_progress(progress_path, tasks)
            remaining = sum(1 for c, _ in tasks if not c)
            return {"marked": task_id, "remaining": remaining}
    return {"error": f"Task not found: {task_id}"}
