"""LLM subprocess wrapper with retry, validation, and atomic writes.

Contract: `call_llm(prompt, out_path, validator, mock_output)` runs LLM_CLI
(prompt piped via stdin), validates stdout, and writes it atomically. Returns
True on success, False on giveup. On giveup it writes `{out_path}.failed` with
the last error so the task is blocked for the rest of the current run.

Cross-run: `rotate_failed_markers(root)` runs once at startup per invocation.
It promotes `*.failed` → `*.failed.previous` (rolling up to .previous.5) so a
fresh run gets a clean retry budget per principle 3 of plan.md.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional


MAX_RETRIES = 3
SUBPROCESS_TIMEOUT_SEC = 600  # Opus with --effort max can be slow.
MIN_OUTPUT_BYTES = 200
PREVIOUS_CHAIN_DEPTH = 5

REFUSAL_PREFIXES = (
    "i cannot", "i can't", "i'm sorry", "i am sorry", "i apologize",
    "as an ai", "sorry, but", "unfortunately, i",
)

# Catches LLM outputs that wrap the entire response in ```lang\n...\n``` despite
# prompt instructions saying otherwise. Anchored at both ends so fences embedded
# inside a larger markdown body are left alone.
_WRAPPING_FENCE_RX = re.compile(
    r"\A\s*```[a-zA-Z0-9_+\-]*\n(.*?)\n```\s*\Z",
    re.DOTALL,
)


def _strip_wrapping_fences(text: str) -> str:
    m = _WRAPPING_FENCE_RX.match(text)
    return m.group(1) if m else text


class _Budget:
    """Thread-safe counter for the -t session call-cap. limit=None is unlimited."""

    def __init__(self) -> None:
        self.limit: Optional[int] = None
        self.used = 0
        self.lock = threading.Lock()

    def set_limit(self, limit: Optional[int]) -> None:
        with self.lock:
            self.limit = limit
            self.used = 0

    def try_acquire(self) -> bool:
        """Reserve one call. Returns False when the cap is already reached."""
        with self.lock:
            if self.limit is not None and self.used >= self.limit:
                return False
            self.used += 1
            return True

    def snapshot(self) -> tuple[int, Optional[int]]:
        with self.lock:
            return self.used, self.limit


_budget = _Budget()


def set_budget(limit: Optional[int]) -> None:
    """Cap LLM subprocess calls for this invocation. None = unlimited."""
    _budget.set_limit(limit)


def budget_snapshot() -> tuple[int, Optional[int]]:
    """Returns (used, limit). limit is None if unlimited."""
    return _budget.snapshot()


def budget_exhausted() -> bool:
    used, limit = _budget.snapshot()
    return limit is not None and used >= limit


def is_mock_mode() -> bool:
    return os.environ.get("PIPELINE_MOCK") == "1"


def _looks_like_refusal(text: str) -> bool:
    stripped = text.strip().lower()
    return any(stripped.startswith(p) for p in REFUSAL_PREFIXES)


def default_validate(text: str) -> Optional[str]:
    """Shape checks for most markdown outputs. Returns reason string on failure.

    Public so task-specific validators in pipeline.py can compose it (e.g. a
    validator that wants the default checks AND an extra structural check).
    Task-specific validators that legitimately produce short output (topic
    'No changes.', JSON dedup decisions) can skip this entirely.
    """
    if not text.strip():
        return "empty output"
    if len(text.encode("utf-8")) < MIN_OUTPUT_BYTES:
        return f"output too short ({len(text)} chars)"
    if _looks_like_refusal(text):
        return "output starts with a refusal/apology"
    return None


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def failed_marker_for(out_path: Path) -> Path:
    return out_path.with_name(out_path.name + ".failed")


def _write_failed(out_path: Path, attempts: list[dict]) -> None:
    marker = failed_marker_for(out_path)
    lines = [f"# Giveup after {len(attempts)} attempts for {out_path.name}", ""]
    for i, a in enumerate(attempts, 1):
        lines.append(f"## Attempt {i}: {a['reason']}")
        if a.get("stderr"):
            lines.append("### stderr")
            lines.append(a["stderr"][-4000:])
        if a.get("stdout"):
            lines.append("### stdout (tail)")
            lines.append(a["stdout"][-2000:])
        lines.append("")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("\n".join(lines), encoding="utf-8")


def _run_cli(prompt: str) -> tuple[int, str, str]:
    """Invoke LLM_CLI once. Returns (exit_code, stdout, stderr)."""
    cli = os.environ.get("LLM_CLI", "").strip()
    if not cli:
        raise RuntimeError("LLM_CLI not set in environment (.env)")
    argv = shlex.split(cli)
    proc = subprocess.run(
        argv,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SEC,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def call_llm(
    prompt: str,
    out_path: Path,
    validator: Optional[Callable[[str], Optional[str]]] = None,
    mock_output: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """Run the LLM for one task with retries. See module docstring.

    validator: returns None on success or a reason string on failure. Applied
    after the default shape checks.
    mock_output: in mock mode this string is written directly, no LLM call.
    log: optional callable for single-line status updates (thread-safe by caller).
    """
    log = log or (lambda _: None)

    if out_path.exists():
        return True
    if failed_marker_for(out_path).exists():
        log(f"[skip] {out_path.name} — .failed from earlier this run")
        return False

    if is_mock_mode():
        if mock_output is None:
            mock_output = "### Mock — High Impact\n- mock.item: 0 -> 1 | placeholder\n"
        _atomic_write(out_path, mock_output)
        log(f"[mock] {out_path.name}")
        return True

    attempts: list[dict] = []
    last_reason: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        if not _budget.try_acquire():
            # Budget exhausted mid-task. Do NOT write .failed — the task must
            # stay pending so a later invocation (with fresh budget) picks it up.
            # Log only when we'd already started the task; otherwise be silent
            # so a parallel step draining post-exhaustion doesn't spam the log.
            if attempts:
                log(f"[budget] {out_path.name} — stopping after {len(attempts)} attempt(s); task left pending")
            return False

        if attempt == 1:
            log(f"[start] {out_path.name}")
        else:
            log(f"[retry {attempt}/{MAX_RETRIES}] {out_path.name} — previous: {last_reason}")

        try:
            rc, stdout, stderr = _run_cli(prompt)
        except subprocess.TimeoutExpired as e:
            last_reason = "subprocess timeout"
            attempts.append({"reason": last_reason, "stderr": str(e), "stdout": ""})
            continue
        except Exception as e:
            last_reason = f"subprocess error: {e!r}"
            attempts.append({"reason": last_reason, "stderr": "", "stdout": ""})
            continue

        if rc != 0:
            last_reason = f"exit {rc}"
            attempts.append({"reason": last_reason, "stderr": stderr, "stdout": stdout})
            continue

        stdout = _strip_wrapping_fences(stdout)

        # A task-specific validator is authoritative when provided — it knows
        # whether short output or special phrases are legitimate for this task.
        # Otherwise, fall through to default shape checks.
        reason = validator(stdout) if validator is not None else default_validate(stdout)
        if reason is not None:
            last_reason = reason
            attempts.append({"reason": reason, "stderr": stderr, "stdout": stdout})
            continue

        _atomic_write(out_path, stdout)
        log(f"[ok] {out_path.name}")
        return True

    _write_failed(out_path, attempts)
    log(f"[fail] {out_path.name} after {MAX_RETRIES} attempts — see {failed_marker_for(out_path).name}")
    return False


def rotate_failed_markers(root: Path) -> int:
    """Promote *.failed → *.failed.previous, rolling older chains up to .previous.4.

    Chain slots (newest → oldest):
        X.failed.previous, X.failed.previous.1, ... X.failed.previous.4
    That's 5 historical entries (PREVIOUS_CHAIN_DEPTH). Anything at slot .4 is
    dropped on next rotation — a task that has failed 5+ consecutive runs.

    Called exactly once at the start of each invocation before any step runs.
    Returns the number of markers rotated.
    """
    if not root.exists():
        return 0

    rotated = 0
    at_ceiling: list[Path] = []

    for marker in sorted(root.rglob("*.failed")):
        # Newest-first chain of previous slots: index 0 = most recent previous.
        slots = [marker.with_name(f"{marker.name}.previous")] + [
            marker.with_name(f"{marker.name}.previous.{i}")
            for i in range(1, PREVIOUS_CHAIN_DEPTH)
        ]
        # Drop the oldest slot if occupied — this task has hit the ceiling.
        if slots[-1].exists():
            at_ceiling.append(marker)
            slots[-1].unlink()
        # Shift each occupied slot one step older.
        for i in range(len(slots) - 2, -1, -1):
            if slots[i].exists():
                slots[i].replace(slots[i + 1])
        # Move current .failed into the newest slot.
        marker.replace(slots[0])
        rotated += 1

    if at_ceiling:
        print(f"!! {len(at_ceiling)} task(s) have failed {PREVIOUS_CHAIN_DEPTH}+ consecutive runs:")
        for m in at_ceiling:
            print(f"   {m}")
        print("   Pipeline will still retry — consider investigating the prompt or input.")
    return rotated
