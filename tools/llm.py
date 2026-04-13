"""LLM subprocess wrapper with retry, validation, and atomic writes.

Contract: `call_llm(prompt, out_path, validator, mock_output)` runs LLM_CLI
(prompt piped via stdin), validates stdout, and writes it atomically. Returns
True on success, False on giveup. On giveup it writes `{out_path}.failed` with
the last error so the task is blocked for the rest of the current run.

Cross-run: `rotate_failed_markers(root)` runs once at startup per invocation.
It promotes `*.failed` → `*.failed.previous` (rolling up to .previous.5) so a
fresh run gets a clean retry budget per principle 3 of plan.md.

Session-halt signals (both short-circuit every subsequent call_llm):
- `auth_failed()`: LLM CLI returned a 401/403/invalid-api-key error. Detected
  from stderr; no point retrying until the user re-auths.
- `preview_aborted()`: user rejected an output shown during the preview gate.

Preview gate: when enabled (`set_preview(True, n=3)`), the first `n` successful
calls of the session are serialized and shown to the user before the next runs.
Rejection deletes the written output(s) and halts the session. Designed so a
user can sanity-check output from a fresh model/key before committing to the
full pipeline fan-out, regardless of which step those first calls come from.
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
PREVIEW_CALL_COUNT = 3
PREVIEW_BODY_LINES = 12
PREVIEW_STDERR_TAIL_CHARS = 400

REFUSAL_PREFIXES = (
    "i cannot", "i can't", "i'm sorry", "i am sorry", "i apologize",
    "as an ai", "sorry, but", "unfortunately, i",
)

# Auth-class errors won't self-heal within one pipeline run — halt the whole
# session instead of burning retries + parallel budget on something that
# requires the user to re-auth.
AUTH_ERROR_RX = re.compile(
    r"\b401\b|\b403\b|unauthorized|invalid[_\- ]?api[_\- ]?key|"
    r"authentication\s+(failed|error)|incorrect\s+api\s+key",
    re.IGNORECASE,
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


def _stderr_tail(stderr: str, limit: int = PREVIEW_STDERR_TAIL_CHARS) -> str:
    if not stderr:
        return ""
    text = stderr.strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


# ---- Budget ---------------------------------------------------------------


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


# ---- Auth halt ------------------------------------------------------------


_auth_halted = False
_auth_halt_lock = threading.Lock()


def auth_failed() -> bool:
    with _auth_halt_lock:
        return _auth_halted


def _looks_like_auth_error(stderr: str) -> bool:
    return bool(stderr) and bool(AUTH_ERROR_RX.search(stderr))


def _halt_for_auth(stderr: str, out_path: Path, log: Callable[[str], None]) -> None:
    global _auth_halted
    with _auth_halt_lock:
        if _auth_halted:
            return
        _auth_halted = True
    log("")
    log(f"!! LLM auth failed on {out_path.name} — halting pipeline.")
    log("!! Re-auth your CLI (e.g. `codex login` or check the API key) and re-run.")
    log("!! stderr tail:")
    for line in _stderr_tail(stderr, limit=600).splitlines():
        log(f"!!   {line}")


# ---- Preview gate ---------------------------------------------------------


class _PreviewGate:
    """Serializes the first N successful LLM calls, prompts user per output.

    Approval: decrement counter; gate closes at 0, parallelism resumes.
    Rejection: delete all preview outputs (approved + in-flight), mark aborted.
    """

    def __init__(self) -> None:
        self.call_lock = threading.Lock()  # serializes call_llm while active
        self._state_lock = threading.Lock()
        self._target = 0
        self._remaining = 0
        self._paths: list[Path] = []
        self._aborted = False

    def enable(self, n: int) -> None:
        with self._state_lock:
            self._target = n
            self._remaining = n
            self._paths = []
            self._aborted = False

    def disable(self) -> None:
        with self._state_lock:
            self._target = 0
            self._remaining = 0
            self._paths = []
            self._aborted = False

    def is_active(self) -> bool:
        with self._state_lock:
            return not self._aborted and self._remaining > 0

    def is_aborted(self) -> bool:
        with self._state_lock:
            return self._aborted

    def approve(self, out_path: Path) -> tuple[int, int]:
        """Record an approval. Returns (approved_so_far, target)."""
        with self._state_lock:
            self._paths.append(out_path)
            self._remaining -= 1
            return self._target - self._remaining, self._target

    def reject(self, include: Optional[Path] = None) -> list[Path]:
        """Abort + delete preview outputs. Returns the deleted paths."""
        with self._state_lock:
            self._aborted = True
            paths = list(self._paths)
            if include is not None and include not in paths:
                paths.append(include)
            self._paths = []
        for p in paths:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        return paths

    def status(self) -> tuple[int, int]:
        """Returns (approved_so_far, target)."""
        with self._state_lock:
            return self._target - self._remaining, self._target


_preview_gate = _PreviewGate()


def set_preview(enabled: bool, n: int = PREVIEW_CALL_COUNT) -> None:
    """Turn the first-N-call preview gate on/off. Call once at session start."""
    if enabled:
        _preview_gate.enable(n)
    else:
        _preview_gate.disable()


def preview_aborted() -> bool:
    return _preview_gate.is_aborted()


def _session_halted() -> bool:
    """Short-circuit check used at the top of every call_llm + each retry."""
    return auth_failed() or preview_aborted()


# ---- Mock mode ------------------------------------------------------------


def is_mock_mode() -> bool:
    return os.environ.get("PIPELINE_MOCK") == "1"


# ---- Validation -----------------------------------------------------------


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


# ---- File IO --------------------------------------------------------------


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


# ---- LLM invocation -------------------------------------------------------


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
    if _session_halted():
        return False

    if is_mock_mode():
        if mock_output is None:
            mock_output = "### Mock — High Impact\n- mock.item: 0 -> 1 | placeholder\n"
        _atomic_write(out_path, mock_output)
        log(f"[mock] {out_path.name}")
        return True

    # Serialize if the preview gate is still open. Other workers block on
    # call_lock; after the Nth approval the gate disables and they all fall
    # through to normal parallel execution.
    if _preview_gate.is_active():
        with _preview_gate.call_lock:
            if _session_halted():
                return False
            if _preview_gate.is_active():
                return _run_with_preview(prompt, out_path, validator, log)
            # Gate closed while we waited — fall through (lock released on exit).

    return _run_retries(prompt, out_path, validator, log) is not None


def _log_fail_attempt(
    log: Callable[[str], None],
    attempt: int,
    out_path: Path,
    reason: str,
    detail: str = "",
) -> None:
    suffix = f": {detail}" if detail else ""
    log(f"[fail-attempt {attempt}/{MAX_RETRIES}] {out_path.name} — {reason}{suffix}")


def _run_retries(
    prompt: str,
    out_path: Path,
    validator: Optional[Callable[[str], Optional[str]]],
    log: Callable[[str], None],
) -> Optional[str]:
    """Retry loop with per-attempt logging and auth fail-fast.

    Returns the validated stdout on success (already written to `out_path`),
    or None on failure/halt. Returning the content lets the preview gate
    avoid a read-back of the just-written file.
    """
    attempts: list[dict] = []
    last_reason: Optional[str] = None
    for attempt in range(1, MAX_RETRIES + 1):
        if _session_halted():
            return None
        if not _budget.try_acquire():
            if attempts:
                log(f"[budget] {out_path.name} — stopping after {len(attempts)} attempt(s); task left pending")
            return None

        if attempt == 1:
            log(f"[start] {out_path.name}")
        else:
            log(f"[retry {attempt}/{MAX_RETRIES}] {out_path.name} — previous: {last_reason}")

        try:
            rc, stdout, stderr = _run_cli(prompt)
        except subprocess.TimeoutExpired as e:
            last_reason = "subprocess timeout"
            attempts.append({"reason": last_reason, "stderr": str(e), "stdout": ""})
            _log_fail_attempt(log, attempt, out_path, last_reason)
            continue
        except Exception as e:
            last_reason = f"subprocess error: {e!r}"
            attempts.append({"reason": last_reason, "stderr": "", "stdout": ""})
            _log_fail_attempt(log, attempt, out_path, last_reason)
            continue

        if rc != 0:
            if _looks_like_auth_error(stderr):
                _halt_for_auth(stderr, out_path, log)
                return None
            last_reason = f"exit {rc}"
            attempts.append({"reason": last_reason, "stderr": stderr, "stdout": stdout})
            _log_fail_attempt(log, attempt, out_path, last_reason, _stderr_tail(stderr))
            continue

        stdout = _strip_wrapping_fences(stdout)

        # A task-specific validator is authoritative when provided — it knows
        # whether short output or special phrases are legitimate for this task.
        reason = validator(stdout) if validator is not None else default_validate(stdout)
        if reason is not None:
            last_reason = reason
            attempts.append({"reason": reason, "stderr": stderr, "stdout": stdout})
            _log_fail_attempt(log, attempt, out_path, reason)
            continue

        _atomic_write(out_path, stdout)
        log(f"[ok] {out_path.name}")
        return stdout

    _write_failed(out_path, attempts)
    log(f"[fail] {out_path.name} after {MAX_RETRIES} attempts — see {failed_marker_for(out_path).name}")
    return None


def _run_with_preview(
    prompt: str,
    out_path: Path,
    validator: Optional[Callable[[str], Optional[str]]],
    log: Callable[[str], None],
) -> bool:
    """Run one call under the preview gate: run, show preview, prompt.

    Caller must hold `_preview_gate.call_lock`. On approval we return True and
    let the caller proceed. On rejection we delete the written output (plus any
    previously approved preview outputs), mark the gate aborted, return False.
    """
    content = _run_retries(prompt, out_path, validator, log)
    if content is None:
        return False  # retry/auth/budget failure — gate state unchanged

    approved_before, target = _preview_gate.status()
    slot = approved_before + 1

    body_lines = content.splitlines()
    total = len(body_lines)
    shown = body_lines[:PREVIEW_BODY_LINES]
    size_bytes = len(content.encode("utf-8"))

    print()
    print(f"─── [preview {slot}/{target}] {out_path.name} ({size_bytes} bytes, {total} lines) ───")
    for line in shown:
        print(line)
    if total > PREVIEW_BODY_LINES:
        print(f"… ({total - PREVIEW_BODY_LINES} more lines truncated)")
    print("─" * 72)

    try:
        resp = input(f"Approve preview {slot}/{target} and continue? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        resp = "n"

    if resp in ("", "y"):
        approved, _ = _preview_gate.approve(out_path)
        if approved >= target:
            log(f"[preview] all {target} approved — resuming normal parallel execution")
        else:
            log(f"[preview] approved ({approved}/{target}) — next call will prompt")
        return True

    deleted = _preview_gate.reject(include=out_path)
    log(f"[preview] rejected — deleted {len(deleted)} preview output(s); halting pipeline")
    return False


# ---- Failed-marker rotation -----------------------------------------------


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
