#!/usr/bin/env python3
"""
Pipeline step 04 — pipe each chunk to the LLM, capture findings.

Usage:
    python3 src/04_llm.py --out DIR --llm-cmd "<shell-command>"
        [--prompt PATH] [--workers N] [--llm-calls N] [--no-approval]

Input:
    <--out>/03_chunk/chunks/*.txt
    --prompt defaults to src/prompt.md next to this script. The prompt text
    is prepended to each chunk (separated by a blank line) before piping to
    the LLM via stdin.

Output:
    <--out>/04_llm/findings/<chunk_id>.md
    The literal stdout from the LLM, or "[none]" if it reported no gameplay change.

Behaviour:
  - The first pending call runs sequentially. Its input + response are
    printed in full and the user is asked to approve continuing. Pass
    `--no-approval` to skip the prompt (for automation).
  - All subsequent calls run concurrently across `--workers` (default 4).
    Atomic rename keeps partial writes invisible.
  - `--llm-calls N` caps the number of *fresh* LLM calls this run (cached
    findings don't count). Retries count against the same cap. If a first
    response indicates that the bounded retry is required but no call budget
    remains, the chunk is left pending and no finding file is written. When
    the cap is reached the step stops cleanly; a later run retries that chunk
    from the start. Default: no cap.
  - Any LLM call that exits non-zero OR returns empty stdout ABORTS the
    pipeline with the full command, returncode, stdout, and stderr. No
    more silent retry loops.

Resumability:
  - Chunks whose finding file already exists are skipped without an LLM call.
    A successful first-pass response does not count as done until the final
    response for that chunk is persisted.
  - Prompt files must exist and be readable. A bad `--prompt` path aborts the
    step before any LLM call or cache write.
"""
import argparse
import json
import shlex
import subprocess
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event, Lock

from _lib import (
    ALLOWED_PREFIXES_LINE_PREFIX,
    CHUNK_HEADER_SEPARATOR_PREFIX,
    Progress,
    atomic_write_text,
    count_changed_lines,
    count_hunks,
)


# --------------------------------------------------------------------------- #
# LLM invocation
# --------------------------------------------------------------------------- #

class LLMError(Exception):
    def __init__(self, cid: str, cmd: str, returncode: int, stdout: str, stderr: str):
        self.cid = cid
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"LLM failure on {cid}")


class CallBudgetExhausted(Exception):
    def __init__(self, *, retry_required: bool = False):
        self.retry_required = retry_required
        super().__init__("LLM call budget exhausted")


class CallBudget:
    def __init__(self, total: int | None):
        self.total = total
        self.used = 0
        self._lock = Lock()

    def consume(self) -> bool:
        if self.total is None:
            return True
        with self._lock:
            if self.used >= self.total:
                return False
            self.used += 1
            return True

    def remaining(self) -> int | None:
        if self.total is None:
            return None
        with self._lock:
            return max(self.total - self.used, 0)


_RECHECK_PROMPT = """# RECHECK CAREFULLY

The previous pass may have missed a subtle gameplay-relevant change.

Re-read the chunk and only return `[none]` if you are confident the diff is purely non-gameplay.

Important:
- Small diffs can still be gameplay-relevant.
- Treat changes to IDs/names as relevant when they rename a gameplay-facing entity or reference in gamestart, spawn, station, job, or script contexts.
- Treat changes to loadout quantity/variation, quotas, hostility/relations, attack permissions, and similar AI/spawn flags as gameplay-relevant even when only one tag or attribute changed.
- Do not assume hidden defaults or old values that are not shown in the chunk.
"""

_FILE_FALLBACK_RECHECK_PROMPT = """# PREFIX RECHECK CAREFULLY

The previous pass used a `[file:...]` prefix even though the chunk provides more specific entity labels.

Re-read the chunk and prefer the most specific allowed entity prefix when the change maps cleanly to a listed entity.

Important:
- The `Allowed prefixes JSON` list is authoritative.
- Use `[file:...]` only for genuinely file-scoped aggregate, preamble, or cross-entity changes that cannot be assigned cleanly.
- If the diff adds, removes, or edits a named entity whose exact label is listed, prefer that exact entity label over `[file:...]`.
"""


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _format_timeout(timeout: float) -> str:
    if float(timeout).is_integer():
        seconds = int(timeout)
        unit = "second" if seconds == 1 else "seconds"
        return f"{seconds} {unit}"
    return f"{timeout:g} seconds"


def run_llm(cmd: str, stdin_text: str, cid: str, timeout: float = 600) -> str:
    """Pipe stdin_text to cmd's stdin. Return stdout (stripped), or raise LLMError."""
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        stdout = _coerce_output(e.stdout)
        stderr = _coerce_output(e.stderr)
        timeout_note = f"LLM command timed out after {_format_timeout(timeout)}."
        if stderr:
            stderr = stderr.rstrip() + "\n" + timeout_note
        else:
            stderr = timeout_note
        raise LLMError(cid, cmd, 124, stdout, stderr) from None
    out = proc.stdout.strip() if proc.stdout else ""
    if proc.returncode != 0 or not out:
        raise LLMError(cid, cmd, proc.returncode, proc.stdout or "", proc.stderr or "")
    return out


def build_stdin(prompt_text: str, chunk_text: str, extra_instructions: str = "") -> str:
    parts: list[str] = []
    if prompt_text:
        parts.append(prompt_text.rstrip())
    if extra_instructions:
        parts.append(extra_instructions.rstrip())
    parts.append(chunk_text)
    return "\n\n".join(parts)


def _split_chunk_header_body(chunk_text: str) -> tuple[str, str]:
    header: list[str] = []
    body: list[str] = []
    in_body = False
    for line in chunk_text.splitlines(keepends=True):
        if not in_body:
            header.append(line)
            if line.startswith(CHUNK_HEADER_SEPARATOR_PREFIX):
                in_body = True
            continue
        body.append(line)
    return "".join(header), "".join(body)


def _parse_allowed_prefixes(chunk_text: str) -> list[str]:
    header, _body = _split_chunk_header_body(chunk_text)
    for line in header.splitlines():
        if not line.startswith(ALLOWED_PREFIXES_LINE_PREFIX):
            continue
        payload = line[len(ALLOWED_PREFIXES_LINE_PREFIX):].strip()
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, str) and item]
    return []


def _parse_response_prefixes(response: str) -> list[str]:
    prefixes: list[str] = []
    for line in response.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) > 2:
            prefixes.append(stripped[1:-1])
    return prefixes


def should_retry_none(chunk_text: str) -> bool:
    allowed_prefixes = _parse_allowed_prefixes(chunk_text)
    if not any(not prefix.startswith("file:") for prefix in allowed_prefixes):
        return False
    _header, body = _split_chunk_header_body(chunk_text)
    hunk_count = count_hunks(body)
    if hunk_count == 0:
        return False
    changed_lines = count_changed_lines(body)
    return (
        0 < changed_lines <= 12
        and hunk_count <= 2
        and len(body.encode("utf-8")) <= 2500
    )


def should_retry_file_fallback(chunk_text: str, response: str) -> bool:
    """Retry when the response collapses to file scope despite exact entity labels.

    This is intentionally model-agnostic. We key off the chunk metadata plus the
    response shape instead of maintaining a list of "weak" model names. Any
    future model that shows the same collapse-to-file behavior gets the same
    bounded recovery path automatically.
    """
    allowed_prefixes = _parse_allowed_prefixes(chunk_text)
    entity_prefixes = [prefix for prefix in allowed_prefixes if not prefix.startswith("file:")]
    if not entity_prefixes:
        return False

    response_prefixes = _parse_response_prefixes(response)
    return bool(response_prefixes) and all(
        prefix.startswith("file:") for prefix in response_prefixes
    )


def run_llm_with_retry(
    cmd: str,
    prompt_text: str,
    chunk_text: str,
    cid: str,
    timeout: float,
    budget: CallBudget | None = None,
) -> tuple[str, bool, str]:
    if budget is not None and not budget.consume():
        raise CallBudgetExhausted()

    stdin_text = build_stdin(prompt_text, chunk_text)
    response = run_llm(cmd, stdin_text, cid, timeout=timeout)
    retry_prompt = ""
    if response.strip() == "[none]":
        if should_retry_none(chunk_text):
            retry_prompt = _RECHECK_PROMPT
    elif should_retry_file_fallback(chunk_text, response):
        retry_prompt = _FILE_FALLBACK_RECHECK_PROMPT

    if not retry_prompt:
        return response, False, stdin_text

    if budget is not None and not budget.consume():
        raise CallBudgetExhausted(retry_required=True)

    retry_stdin = build_stdin(prompt_text, chunk_text, extra_instructions=retry_prompt)
    retry_response = run_llm(
        cmd,
        retry_stdin,
        cid,
        timeout=timeout,
    )
    return retry_response, True, retry_stdin


def summarize_response(response: str, *, retried: bool = False) -> str:
    summary = "[none]" if response.strip() == "[none]" else f"{len(response)}B"
    return f"{summary}; retried" if retried else summary


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in (text or "").splitlines()) or f"{pad}(empty)"


def show_llm_error(e: "LLMError") -> None:
    sep = "─" * 60
    print(f"\n[04_llm] ABORT: LLM failed on chunk {e.cid}", file=sys.stderr)
    print(f"  command:    {e.cmd}", file=sys.stderr)
    print(f"  returncode: {e.returncode}", file=sys.stderr)
    print(f"  stdout:\n{sep}\n{_indent(e.stdout, 2)}\n{sep}", file=sys.stderr)
    print(f"  stderr:\n{sep}\n{_indent(e.stderr, 2)}\n{sep}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# First-call preview + approval
# --------------------------------------------------------------------------- #

def preview_first_call(cid: str, stdin_text: str, response: str) -> None:
    sep = "=" * 70
    print(f"\n{sep}", file=sys.stderr)
    print(f"First LLM call — chunk {cid}", file=sys.stderr)
    print(sep, file=sys.stderr)
    preview = stdin_text
    if len(preview) > 4000:
        preview = (
            preview[:2000]
            + f"\n... [{len(stdin_text) - 4000} chars elided] ...\n"
            + preview[-2000:]
        )
    print(f"--- Input ({len(stdin_text)} chars) ---", file=sys.stderr)
    print(preview, file=sys.stderr)
    print(f"--- Response ({len(response)} chars) ---", file=sys.stderr)
    print(response, file=sys.stderr)
    print(sep, file=sys.stderr)


def ask_continue(remaining: int) -> bool:
    """Blocking prompt on the controlling TTY. Returns True iff user types y/yes."""
    prompt = (
        f"Continue with remaining {remaining} chunk(s)? "
        f"(y = proceed, anything else = abort) [y/N]: "
    )
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        answer = sys.stdin.readline()
    except KeyboardInterrupt:
        return False
    return answer.strip().lower() in ("y", "yes")


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #

def _process(
    chunk_path: Path,
    cid: str,
    out_path: Path,
    llm_cmd: str,
    prompt_text: str,
    timeout_sec: float,
    progress: Progress,
    cancel: Event,
    budget: CallBudget | None,
) -> bool:
    if cancel.is_set():
        return False
    chunk_text = chunk_path.read_text(encoding="utf-8")
    try:
        response, retried, _stdin_text = run_llm_with_retry(
            llm_cmd,
            prompt_text,
            chunk_text,
            cid,
            timeout_sec,
            budget=budget,
        )
    except CallBudgetExhausted:
        return False
    n = progress.tick(f"→ {cid}")
    atomic_write_text(out_path, response + "\n")
    progress.at(n, f"← {cid} ({summarize_response(response, retried=retried)})")
    return True


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    p = argparse.ArgumentParser(description="Pipe each chunk to the LLM and save findings.")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--llm-cmd", required=True)
    p.add_argument(
        "--prompt", type=Path, default=None,
        help="Path to the system prompt (default: src/prompt.md next to this script).",
    )
    p.add_argument(
        "--workers", type=int, default=4,
        help="Parallel LLM workers after first-call approval (default: 4).",
    )
    p.add_argument(
        "--llm-calls", type=int, default=None,
        help="Cap on fresh LLM calls this run (cached chunks don't count). "
             "Retries count against the same cap; if a retry is still needed "
             "when the cap is hit, the chunk stays pending with no finding "
             "file. Stops cleanly at the limit; re-run to continue. "
             "Default: no cap.",
    )
    p.add_argument(
        "--no-approval", action="store_true",
        help="Skip the first-call preview + approval prompt.",
    )
    p.add_argument(
        "--timeout-sec", type=float, default=600,
        help="Per-LLM-call timeout in seconds, including one bounded retry when used (default: 600).",
    )
    args = p.parse_args()

    chunks_dir = args.out / "03_chunk" / "chunks"
    findings_dir = args.out / "04_llm" / "findings"
    prompt_path = args.prompt if args.prompt is not None else Path(__file__).parent / "prompt.md"
    try:
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"[04_llm] ABORT: cannot read prompt file {prompt_path}: {exc}") from exc

    chunk_paths = sorted(chunks_dir.glob("*.txt"))
    progress = Progress("04_llm", len(chunk_paths))

    # Partition: cached (tick immediately) vs pending.
    pending: list[tuple[Path, str, Path]] = []
    skipped = 0
    for cp in chunk_paths:
        cid = cp.stem
        out_path = findings_dir / f"{cid}.md"
        if out_path.exists():
            progress.tick(f"skip {cid} (cached)")
            skipped += 1
        else:
            pending.append((cp, cid, out_path))

    if not pending:
        progress.log(f"all {len(chunk_paths)} chunks already have findings")
        return

    if args.llm_calls is not None and args.llm_calls <= 0:
        progress.log(
            f"--llm-calls={args.llm_calls}: no calls this run; {len(pending)} pending. "
            f"Re-run to continue."
        )
        return
    budget = CallBudget(args.llm_calls)

    # ---- First call: sequential, possibly with approval gate ----
    first_cp, first_cid, first_out = pending[0]
    chunk_text = first_cp.read_text(encoding="utf-8")
    n = progress.tick(f"→ {first_cid}")
    try:
        first_response, first_retried, stdin_text = run_llm_with_retry(
            args.llm_cmd,
            prompt_text,
            chunk_text,
            first_cid,
            args.timeout_sec,
            budget=budget,
        )
    except CallBudgetExhausted as exc:
        detail = (
            "retry required; budget exhausted; chunk left pending"
            if exc.retry_required else
            "budget exhausted"
        )
        progress.at(n, f"pause {first_cid} ({detail})")
        _log_summary(progress, 0, skipped, len(pending))
        return
    except LLMError as e:
        show_llm_error(e)
        sys.exit(1)
    progress.at(n, f"← {first_cid} ({summarize_response(first_response, retried=first_retried)})")

    remaining = pending[1:]
    if not args.no_approval:
        preview_first_call(first_cid, stdin_text, first_response)
        remaining_budget = budget.remaining()
        follow_up = len(remaining) if remaining_budget is None else min(len(remaining), remaining_budget)
        if follow_up > 0:
            if not ask_continue(follow_up):
                progress.log("aborted by user; no findings written this run")
                sys.exit(1)

    atomic_write_text(first_out, first_response + "\n")
    wrote = 1

    if not remaining:
        _log_summary(progress, wrote, skipped, len(pending) - wrote)
        return

    # ---- Remaining calls: concurrent ----
    cancel = Event()
    errors: list[LLMError] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = [
            ex.submit(
                _process,
                cp,
                cid,
                out_path,
                args.llm_cmd,
                prompt_text,
                args.timeout_sec,
                progress,
                cancel,
                budget,
            )
            for cp, cid, out_path in remaining
        ]
        for future in as_completed(futures):
            try:
                if future.result():
                    wrote += 1
            except CancelledError:
                continue
            except LLMError as e:
                errors.append(e)
                cancel.set()
                for f in futures:
                    f.cancel()

    if errors:
        show_llm_error(errors[0])
        if len(errors) > 1:
            print(
                f"\n[04_llm] {len(errors)} total failures (showing first). "
                f"Re-run after fixing; cached findings persist.",
                file=sys.stderr,
            )
        sys.exit(1)

    _log_summary(progress, wrote, skipped, len(pending) - wrote)


def _log_summary(progress: Progress, wrote: int, skipped: int, paused: int) -> None:
    parts = [f"wrote {wrote}", f"skipped {skipped}", "failed 0"]
    if paused > 0:
        parts.append(f"paused {paused} (--llm-calls cap; re-run to continue)")
    progress.log(", ".join(parts))


if __name__ == "__main__":
    main()
