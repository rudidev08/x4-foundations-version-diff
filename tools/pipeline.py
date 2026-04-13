#!/usr/bin/env python3
"""X4 diff pipeline orchestrator.

Usage:
    python3 tools/pipeline.py V1 V2 [-j N] [--mock]

Runs the end-to-end pipeline for one version pair and one model (LLM_MODEL
from .env). File-existence checkpointing: every step is idempotent — a rerun
only does what the last run didn't finish. See plan.md for the full design.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chunking
import llm
import prompts


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "source"
DIFF_DIR = ROOT / "diff"
RESULTS_DIR = ROOT / "diff-results"

_log_lock = threading.Lock()


def log(msg: str) -> None:
    with _log_lock:
        print(msg, flush=True)


def _budget_tail() -> str:
    """Short ' | budget: U/L used (R remaining)' suffix, or '' when unlimited."""
    used, limit = llm.budget_snapshot()
    if limit is None:
        return ""
    return f" | budget: {used}/{limit} used ({max(0, limit - used)} remaining)"


def _categorize(out_paths: list[Path]) -> tuple[int, int, int]:
    """Classify task results by filesystem state: (ok, failed, pending).

    ok      — output file written
    failed  — .failed marker present (retries exhausted)
    pending — neither (budget cut off mid-task; will retry next run)
    """
    ok = sum(1 for op in out_paths if op.exists())
    failed = sum(1 for op in out_paths if llm.failed_marker_for(op).exists())
    return ok, failed, len(out_paths) - ok - failed


def _fmt_counts(ok: int, failed: int, pending: int) -> str:
    parts = [f"{ok} ok"]
    if failed:
        parts.append(f"{failed} failed")
    if pending:
        parts.append(f"{pending} pending (budget)")
    return ", ".join(parts)


# --- .env loading -----------------------------------------------------------


REQUIRED_ENV = ("LLM_CLI", "LLM_MODEL", "LLM_CHUNK_SIZE")


def load_env() -> dict[str, str]:
    env_file = ROOT / ".env"
    if not env_file.exists():
        sys.exit(f"Missing {env_file}. Copy .env.example and fill it in.")
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)

    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing required env vars in .env: {', '.join(missing)}")

    try:
        chunk_kb = int(os.environ["LLM_CHUNK_SIZE"])
    except ValueError:
        sys.exit(f"LLM_CHUNK_SIZE must be an integer (KB), got: {os.environ['LLM_CHUNK_SIZE']}")

    return {
        "LLM_CLI": os.environ["LLM_CLI"],
        "LLM_MODEL": os.environ["LLM_MODEL"],
        "LLM_CHUNK_SIZE_KB": chunk_kb,
    }


def print_model_banner(env: dict, countdown: int) -> None:
    label = f"LLM_MODEL = {env['LLM_MODEL']}    CHUNK = {env['LLM_CHUNK_SIZE_KB']} KB"
    width = max(len(label) + 4, 56)
    print("╔" + "═" * width + "╗")
    print("║  " + label.ljust(width - 2) + "║")
    print("╚" + "═" * width + "╝")
    if countdown > 0:
        print(f"Starting in {countdown}s — Ctrl+C to abort if anything looks wrong.")
        try:
            time.sleep(countdown)
        except KeyboardInterrupt:
            sys.exit("Aborted.")


# --- Step 1: raw diff ------------------------------------------------------


def step1_raw_diff(v1: str, v2: str, raw_dir: Path) -> None:
    if raw_dir.exists() and any(raw_dir.rglob("*.diff")):
        log(f"[step 1/9] raw diffs present at {raw_dir.relative_to(ROOT)}")
        return
    log(f"[step 1/9] generating raw diffs {v1} -> {v2}")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "diff.py"), v1, v2],
        cwd=ROOT,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Step 1 failed (tools/diff.py exited {result.returncode})")


# --- Step 2: pin settings --------------------------------------------------


def step2_pin_settings(model_root: Path, env: dict) -> None:
    settings_path = model_root / "settings.json"
    if settings_path.exists():
        existing = json.loads(settings_path.read_text())
        pinned = existing.get("LLM_CHUNK_SIZE_KB")
        current = env["LLM_CHUNK_SIZE_KB"]
        if pinned != current:
            log(
                f"[step 2/9] !! settings mismatch at {settings_path.relative_to(ROOT)}: "
                f"pinned {pinned} KB, .env says {current} KB — keeping pinned value"
            )
        else:
            log(f"[step 2/9] settings pinned ({pinned} KB)")
        return
    model_root.mkdir(parents=True, exist_ok=True)
    settings = {
        "LLM_CHUNK_SIZE_KB": env["LLM_CHUNK_SIZE_KB"],
        "pinned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    settings_path.write_text(json.dumps(settings, indent=2))
    log(f"[step 2/9] pinned {env['LLM_CHUNK_SIZE_KB']} KB to {settings_path.relative_to(ROOT)}")


# --- Step 3: chunk ---------------------------------------------------------


def step3_chunk(raw_dir: Path, chunks_dir: Path, pinned_kb: int) -> list[dict]:
    manifest_path = chunks_dir / "_manifest.json"
    if manifest_path.exists():
        log(f"[step 3/9] chunks present ({manifest_path.relative_to(ROOT)})")
        return json.loads(manifest_path.read_text())
    log(f"[step 3/9] chunking raw diffs into <= {pinned_kb} KB domain groups")
    manifest = chunking.prepare_chunks(raw_dir, chunks_dir, pinned_kb)
    total_kb = sum(e["size_kb"] for e in manifest)
    log(f"[step 3/9] {len(manifest)} chunks, {total_kb:.0f} KB total")
    return manifest


# --- Step 4: analyze -------------------------------------------------------


def _analysis_path(analysis_dir: Path, chunk_stem: str, variant: str) -> Path:
    if variant == "general":
        return analysis_dir / f"{chunk_stem}.md"
    return analysis_dir / f"{chunk_stem}--{variant}.md"


def _build_analyze_prompt(v1: str, v2: str, entry: dict, variant: str, chunk_path: Path) -> str:
    diff_text = chunk_path.read_text(encoding="utf-8")
    label = entry["label"]
    if variant == "general":
        return prompts.analyze_general(v1, v2, label, diff_text)
    if variant == "mechanics":
        return prompts.analyze_localization_mechanics(v1, v2, label, diff_text)
    if variant == "lore":
        return prompts.analyze_localization_lore(v1, v2, label, diff_text)
    raise ValueError(f"unknown variant: {variant}")


def _validate_markdown_analysis(text: str) -> str | None:
    if reason := llm.default_validate(text):
        return reason
    if "###" not in text:
        return "no ### subheaders — likely an unstructured response"
    return None


def step4_analyze(v1: str, v2: str, manifest: list[dict], chunks_dir: Path,
                  analysis_dir: Path, concurrency: int) -> tuple[int, int]:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[dict, str, Path, Path]] = []
    for entry in manifest:
        chunk_path = chunks_dir / entry["file"]
        chunk_stem = chunk_path.stem
        for variant in entry["analysis_variants"]:
            out_path = _analysis_path(analysis_dir, chunk_stem, variant)
            if out_path.exists():
                continue
            tasks.append((entry, variant, chunk_path, out_path))

    if not tasks:
        log("[step 4/9] all analyses present")
        return 0, 0

    if llm.budget_exhausted():
        log(f"[step 4/9] skipped — session budget already exhausted ({len(tasks)} task(s) left pending)")
        return 0, len(tasks)

    log(f"[step 4/9] {len(tasks)} analyses to run (concurrency={concurrency})")

    def worker(task):
        entry, variant, chunk_path, out_path = task
        prompt = _build_analyze_prompt(v1, v2, entry, variant, chunk_path)
        mock = prompts.MOCK_ANALYZE if llm.is_mock_mode() else None
        return llm.call_llm(
            prompt, out_path,
            validator=_validate_markdown_analysis,
            mock_output=mock,
            log=log,
        )

    _run_parallel(tasks, worker, concurrency)
    ok, failed, pending = _categorize([t[3] for t in tasks])
    log(f"[step 4/9] done — {_fmt_counts(ok, failed, pending)}{_budget_tail()}")
    return failed, pending


# --- Step 5: per-domain consolidation (deterministic) ----------------------


def step5_concat_domains(manifest: list[dict], analysis_dir: Path, by_domain_dir: Path) -> None:
    """Always rewrite. Deterministic + cheap. Regenerating each run correctly
    handles partial Step-4 runs where some analyses only just arrived."""
    by_domain_dir.mkdir(parents=True, exist_ok=True)

    # Group analyses by (domain, variant) → list of (part_number, file_path)
    groups: dict[tuple[str, str], list[tuple[int, Path]]] = defaultdict(list)
    for entry in manifest:
        domain = entry["domain"]
        chunk_stem = Path(entry["file"]).stem
        part = entry.get("part") or 1
        for variant in entry["analysis_variants"]:
            src = _analysis_path(analysis_dir, chunk_stem, variant)
            if src.exists():
                groups[(domain, variant)].append((part, src))

    written = 0
    for (domain, variant), parts in groups.items():
        parts.sort()
        slug = domain.replace("/", "--")
        out_path = by_domain_dir / (f"{slug}.md" if variant == "general" else f"{slug}--{variant}.md")

        if len(parts) == 1:
            content = parts[0][1].read_text(encoding="utf-8")
        else:
            sections = [
                f"## Part {part}\n\n{path.read_text(encoding='utf-8').strip()}\n"
                for part, path in parts
            ]
            content = "\n".join(sections)
        out_path.write_text(content, encoding="utf-8")
        written += 1

    log(f"[step 5/9] consolidated {written} domain file(s)")


# --- Step 6: topic synthesis -----------------------------------------------


def _resolve_topic_sources(topic: dict, by_domain_dir: Path) -> list[tuple[str, str]]:
    """Map a topic's `domains` list to actual analysis-by-domain file contents."""
    domain_spec = topic["domains"]
    all_files = sorted(by_domain_dir.glob("*.md"))

    picked: list[Path] = []
    for spec in domain_spec:
        if spec == "*":
            picked = all_files
            break
        if spec == "extensions":
            picked.extend(f for f in all_files if f.stem.startswith("extensions--"))
        elif spec == "localization_mechanics":
            picked.extend(f for f in all_files if f.stem.endswith("--mechanics"))
        elif spec == "localization_lore":
            picked.extend(f for f in all_files if f.stem.endswith("--lore"))
        else:
            slug = spec.replace("/", "--")
            picked.extend(f for f in all_files if f.stem == slug)

    # De-dup while preserving order.
    seen: set[Path] = set()
    sources: list[tuple[str, str]] = []
    for f in picked:
        if f in seen:
            continue
        seen.add(f)
        sources.append((f.stem, f.read_text(encoding="utf-8")))
    return sources


def _validate_topic_output(text: str) -> str | None:
    stripped = text.strip()
    # The topic prompt explicitly allows this exact short response when no
    # sources have anything relevant — bypass the default length check.
    if stripped == "No changes.":
        return None
    if reason := llm.default_validate(text):
        return reason
    if "###" not in stripped:
        return "topic output has no ### subheaders and isn't 'No changes.'"
    return None


def step6_topics(v1: str, v2: str, by_domain_dir: Path, topics_dir: Path,
                 concurrency: int) -> tuple[int, int]:
    topics_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[dict, Path]] = []
    for topic in prompts.TOPICS:
        out_path = topics_dir / f"{topic['id']}.md"
        if out_path.exists():
            continue
        tasks.append((topic, out_path))

    if not tasks:
        log("[step 6/9] all topics synthesized")
        return 0, 0

    if llm.budget_exhausted():
        log(f"[step 6/9] skipped — session budget already exhausted ({len(tasks)} task(s) left pending)")
        return 0, len(tasks)

    log(f"[step 6/9] {len(tasks)} topics to synthesize (concurrency={concurrency})")

    def worker(task):
        topic, out_path = task
        sources = _resolve_topic_sources(topic, by_domain_dir)
        if not sources:
            out_path.write_text("No changes.\n", encoding="utf-8")
            log(f"[step 6/9] {topic['id']} — no sources, writing No changes.")
            return True
        prompt = prompts.topic_synthesis(v1, v2, topic, sources)
        mock = prompts.MOCK_TOPIC if llm.is_mock_mode() else None
        return llm.call_llm(
            prompt, out_path,
            validator=_validate_topic_output,
            mock_output=mock,
            log=log,
        )

    _run_parallel(tasks, worker, concurrency)
    ok, failed, pending = _categorize([t[1] for t in tasks])
    log(f"[step 6/9] done — {_fmt_counts(ok, failed, pending)}{_budget_tail()}")
    return failed, pending


# --- Step 7: dedup decide --------------------------------------------------


_ENTITY_RX_BACKTICK = re.compile(r"`([^`\n]{2,60})`")
_ENTITY_RX_CAPITAL = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})\b")
_ENTITY_RX_STAT = re.compile(r"([a-z_][\w.]*)\s*:\s*[\d.]+\s*(?:->|→)\s*[\d.]+", re.I)


def _extract_entities(text: str) -> set[str]:
    ents: set[str] = set()
    for m in _ENTITY_RX_BACKTICK.finditer(text):
        ents.add(m.group(1).strip().lower())
    for m in _ENTITY_RX_CAPITAL.finditer(text):
        ents.add(m.group(1).lower())
    for m in _ENTITY_RX_STAT.finditer(text):
        ents.add(m.group(1).lower())
    return ents


def _find_candidate_pairs(topic_contents: dict[str, str]) -> list[tuple[str, str]]:
    """Pairs with >= 2 shared entity tokens are candidates for LLM dedup."""
    active = {
        name: _extract_entities(c)
        for name, c in topic_contents.items()
        if c.strip() and c.strip() != "No changes."
    }
    pairs: list[tuple[str, str]] = []
    for a, b in itertools.combinations(sorted(active), 2):
        if len(active[a] & active[b]) >= 2:
            pairs.append((a, b))
    return pairs


def _validate_dedup_json(text: str) -> str | None:
    # JSON dedup output is intentionally terse (empty-array case is ~40 chars).
    # Skip default length/refusal checks — a refusal would fail JSON parsing anyway.
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError as e:
        return f"not valid JSON: {e}"
    if not isinstance(data, dict):
        return "output is not a JSON object"
    for key in ("remove_from_a", "remove_from_b"):
        if key not in data:
            return f"missing key: {key}"
        if not isinstance(data[key], list) or not all(isinstance(x, str) for x in data[key]):
            return f"{key} must be a list of strings"
    return None


def step7_dedup_decide(topics_dir: Path, decisions_dir: Path, concurrency: int) -> tuple[int, int]:
    # Gate on completeness: deduping a partial set of topics would produce
    # wrong decisions (bullets in unfinished topics would escape dedup).
    missing = [t["id"] for t in prompts.TOPICS if not (topics_dir / f"{t['id']}.md").exists()]
    if missing:
        preview = ", ".join(missing[:5]) + ("..." if len(missing) > 5 else "")
        log(f"[step 7/9] skipped — {len(missing)} topic(s) not yet generated: {preview}")
        return 0, 0

    decisions_dir.mkdir(parents=True, exist_ok=True)
    topic_contents = {
        p.stem: p.read_text(encoding="utf-8") for p in sorted(topics_dir.glob("*.md"))
    }
    pairs = _find_candidate_pairs(topic_contents)
    log(f"[step 7/9] {len(pairs)} candidate pairs after heuristic pre-filter")

    tasks: list[tuple[str, str, Path]] = []
    for a, b in pairs:
        out_path = decisions_dir / f"{a}--vs--{b}.json"
        if out_path.exists():
            continue
        tasks.append((a, b, out_path))

    if not tasks:
        log("[step 7/9] all decisions present")
        return 0, 0

    if llm.budget_exhausted():
        log(f"[step 7/9] skipped — session budget already exhausted ({len(tasks)} task(s) left pending)")
        return 0, len(tasks)

    log(f"[step 7/9] {len(tasks)} dedup decisions to make (concurrency={concurrency})")

    def worker(task):
        a, b, out_path = task
        prompt = prompts.dedup_decide(a, topic_contents[a], b, topic_contents[b])
        mock = prompts.MOCK_DEDUP_JSON if llm.is_mock_mode() else None
        return llm.call_llm(
            prompt, out_path,
            validator=_validate_dedup_json,
            mock_output=mock,
            log=log,
        )

    _run_parallel(tasks, worker, concurrency)
    ok, failed, pending = _categorize([t[2] for t in tasks])
    log(f"[step 7/9] done — {_fmt_counts(ok, failed, pending)}{_budget_tail()}")
    return failed, pending


# --- Step 8: dedup apply ---------------------------------------------------


_BULLET_RX = re.compile(r"^\s*[-*] ")
_TOP_BULLET_RX = re.compile(r"^[-*] ")
_INDENTED_BULLET_RX = re.compile(r"^[ \t]+[-*] ")
# `**Something:**` on its own line — used by topic outputs to anchor a group of
# bullets inside a `###` subsection.
_BOLD_ANCHOR_RX = re.compile(r"^\*\*.+:\*\*\s*$")


def _is_bullet(line: str) -> bool:
    return bool(_TOP_BULLET_RX.match(line) or _INDENTED_BULLET_RX.match(line))


def _is_anchor(line: str) -> bool:
    return line.startswith("### ") or bool(_BOLD_ANCHOR_RX.match(line))


def _has_indented_children(lines: list[str], i: int) -> bool:
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    return j < len(lines) and bool(_INDENTED_BULLET_RX.match(lines[j]))


def _scan_anchors(lines: list[str]) -> list[tuple[int, int]]:
    """For each anchor line (### or **bold:**), return (start, end_exclusive).

    `###` scope ends at the next `###`/`## `/`# ` heading. `**bold:**` scope
    ends at the next anchor of any kind (including a sibling bold).
    """
    n = len(lines)
    positions = [i for i in range(n) if _is_anchor(lines[i])]
    result: list[tuple[int, int]] = []
    for idx, start in enumerate(positions):
        is_sharp = lines[start].startswith("### ")
        end = n
        for j_pos in positions[idx + 1:]:
            if is_sharp:
                if lines[j_pos].startswith(("### ", "## ", "# ")):
                    end = j_pos
                    break
            else:
                end = j_pos
                break
        result.append((start, end))
    return result


def _anchors_had_bullets(content: str) -> dict[str, bool]:
    """Snapshot: map each distinct anchor line text → True iff its scope
    originally contained any bullet. Same-text anchors merge with OR."""
    lines = content.split("\n")
    out: dict[str, bool] = {}
    for a_start, a_end in _scan_anchors(lines):
        key = lines[a_start].strip()
        has = any(_BULLET_RX.match(lines[i]) for i in range(a_start + 1, a_end))
        out[key] = out.get(key, False) or has
    return out


def _classify_removals(removals: set[str]) -> tuple[set[str], set[str], int]:
    """Partition removals into (bullet_targets, heading_targets, ignored_count).

    Normalizes multi-line entries to their first line — cascade handles child
    bullets, and the heading-removal pass handles whole `### Heading\\n\\nbody`
    blocks that the LLM occasionally returns despite the single-line rule.
    """
    bullets: set[str] = set()
    headings: set[str] = set()
    ignored = 0
    for r in removals:
        first = r.split("\n", 1)[0].strip()
        if _is_bullet(first):
            bullets.add(first)
        elif first.startswith("### "):
            headings.add(first)
        else:
            ignored += 1
    return bullets, headings, ignored


def _apply_bullet_and_heading_removals(
    content: str, bullet_targets: set[str], heading_targets: set[str]
) -> tuple[str, set[str]]:
    """Drop `### Heading` subsections the LLM flagged as duplicates, then drop
    individual bullets with cascade of their indented children."""
    lines = content.split("\n")
    n = len(lines)
    keep = [True] * n
    matched: set[str] = set()

    if heading_targets:
        for a_start, a_end in _scan_anchors(lines):
            if not lines[a_start].startswith("### "):
                continue
            key = lines[a_start].strip()
            if key in heading_targets:
                matched.add(key)
                for k in range(a_start, a_end):
                    keep[k] = False

    def cascade_children(start: int) -> None:
        j = start + 1
        while j < n:
            if not keep[j]:
                j += 1
                continue
            line_j = lines[j]
            stripped_j = line_j.strip()
            if not stripped_j:
                keep[j] = False
                j += 1
                continue
            if not _INDENTED_BULLET_RX.match(line_j):
                return
            keep[j] = False
            if stripped_j in bullet_targets:
                matched.add(stripped_j)
            j += 1

    for i in range(n):
        if not keep[i]:
            continue
        stripped = lines[i].strip()
        if stripped not in bullet_targets:
            continue
        keep[i] = False
        matched.add(stripped)
        if _TOP_BULLET_RX.match(lines[i]) and _has_indented_children(lines, i):
            cascade_children(i)

    return "\n".join(lines[k] for k in range(n) if keep[k]), matched


def _drop_emptied_anchors(content: str, had_bullets: dict[str, bool]) -> str:
    """Drop anchors whose scope originally had bullets but has none remaining.

    Processes `###` first so a fully emptied section sweeps away any nested
    `**bold:**` children with it. Prose-only anchors (that never had bullets)
    are left untouched — they weren't emptied by dedup.
    """
    lines = content.split("\n")
    n = len(lines)
    keep = [True] * n

    def live_bullet_in(start: int, end: int) -> bool:
        return any(keep[i] and _BULLET_RX.match(lines[i]) for i in range(start + 1, end))

    anchors = _scan_anchors(lines)
    for sharp_pass in (True, False):
        for a_start, a_end in anchors:
            is_sharp = lines[a_start].startswith("### ")
            if is_sharp != sharp_pass:
                continue
            if not keep[a_start]:
                continue
            key = lines[a_start].strip()
            if not had_bullets.get(key, False):
                continue
            if live_bullet_in(a_start, a_end):
                continue
            for k in range(a_start, a_end):
                keep[k] = False

    return "\n".join(lines[i] for i in range(n) if keep[i])


def _apply_removals(content: str, removals: set[str]) -> tuple[str, set[str], int]:
    """Apply dedup removals and clean up subsection anchors they strand.

    Handles three shapes the LLM returns:
      - `- bullet` — remove line, cascade indented children.
      - `- bullet\\n  - child ...` (multiline) — normalized to first line; cascade
        handles the children.
      - `### Heading` (optionally with body attached) — remove the whole ### scope.

    Non-anchor prose entries (e.g. bold intros the LLM returns by mistake) are
    counted as ignored. After removals, any `###` or `**bold:**` anchor that
    originally carried bullets but now has none gets its whole scope dropped —
    prose-only anchors are preserved as-is.

    Returns (new_content, matched_targets, ignored_non_bullets).
    """
    had_bullets = _anchors_had_bullets(content)
    bullet_targets, heading_targets, ignored = _classify_removals(removals)
    content, matched = _apply_bullet_and_heading_removals(content, bullet_targets, heading_targets)
    content = _drop_emptied_anchors(content, had_bullets)
    return content, matched, ignored


def step8_dedup_apply(topics_dir: Path, decisions_dir: Path, deduped_dir: Path) -> None:
    """Always rewrite. Applies whatever decisions currently exist — on partial
    runs this is a no-op copy, and on the run that completes dedup this is the
    real deduplication pass."""
    deduped_dir.mkdir(parents=True, exist_ok=True)
    to_remove: dict[str, set[str]] = defaultdict(set)

    for decision_file in sorted(decisions_dir.glob("*.json")):
        try:
            data = json.loads(decision_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"[step 8/9] !! skipping malformed {decision_file.name}")
            continue
        a, b = decision_file.stem.split("--vs--", 1)
        for bullet in data.get("remove_from_a", []):
            to_remove[a].add(bullet.strip())
        for bullet in data.get("remove_from_b", []):
            to_remove[b].add(bullet.strip())

    unmatched_total = 0
    ignored_total = 0
    for topic_file in sorted(topics_dir.glob("*.md")):
        content = topic_file.read_text(encoding="utf-8")
        removals = to_remove.get(topic_file.stem, set())
        if removals:
            content, matched, ignored = _apply_removals(content, removals)
            bullets, headings, _ = _classify_removals(removals)
            unmatched = (bullets | headings) - matched
            unmatched_total += len(unmatched)
            ignored_total += ignored
        (deduped_dir / topic_file.name).write_text(content, encoding="utf-8")

    log(f"[step 8/9] applied {sum(len(v) for v in to_remove.values())} total removals"
        + (f" ({ignored_total} non-bullet items skipped)" if ignored_total else "")
        + (f" ({unmatched_total} bullets didn't match source text — LLM drift)" if unmatched_total else ""))


# --- Step 9: assemble ------------------------------------------------------


def step9_assemble(v1: str, v2: str, deduped_dir: Path, model: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{v1}-{v2}-{model}.md"
    if out_path.exists():
        log(f"[step 9/9] result present: {out_path.relative_to(ROOT)}")
        return

    # Gate on completeness: never publish a partial deliverable. Every topic
    # must be in topics-deduped/ (the dedup-apply step mirrors topics/ into it).
    missing = [t["id"] for t in prompts.TOPICS if not (deduped_dir / f"{t['id']}.md").exists()]
    if missing:
        preview = ", ".join(missing[:5]) + ("..." if len(missing) > 5 else "")
        log(f"[step 9/9] skipped — {len(missing)} topic(s) not yet generated: {preview}")
        return

    parts: list[str] = []
    parts.append(f"# X4 Foundations — Changes {v1} → {v2}\n")
    parts.append(f"_Generated by model `{model}`._\n")
    parts.append("## Table of Contents\n")
    for topic in prompts.TOPICS:
        topic_file = deduped_dir / f"{topic['id']}.md"
        if not topic_file.exists():
            continue
        body = topic_file.read_text(encoding="utf-8").strip()
        if not body or body == "No changes.":
            continue
        parts.append(f"- [{topic['label']}](#{topic['id']})")
    parts.append("")

    for topic in prompts.TOPICS:
        topic_file = deduped_dir / f"{topic['id']}.md"
        if not topic_file.exists():
            continue
        body = topic_file.read_text(encoding="utf-8").strip()
        if not body or body == "No changes.":
            continue
        parts.append(f"\n## <a id=\"{topic['id']}\"></a>{topic['label']}\n")
        parts.append(body)
        parts.append("")

    out_path.write_text("\n".join(parts), encoding="utf-8")
    log(f"[step 9/9] wrote {out_path.relative_to(ROOT)}")


# --- Parallel runner -------------------------------------------------------


def _run_parallel(tasks: list, worker, concurrency: int) -> None:
    """Drain worker(task) over tasks with ThreadPoolExecutor. Results are read
    from filesystem (via `_categorize`) afterwards, so the return value of each
    worker is irrelevant here — we only surface crashes."""
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(worker, t) for t in tasks]
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                log(f"[error] worker crashed: {e!r}")


# --- Entry point -----------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Run X4 diff pipeline for one version pair.")
    parser.add_argument("v1", help="Old version (e.g. 9.00B4)")
    parser.add_argument("v2", help="New version (e.g. 9.00B5)")
    parser.add_argument("-j", type=int, default=1,
                        help="Concurrent LLM calls for Steps 4, 6, 7 (default 1; recommended 5)")
    parser.add_argument("-t", type=int, default=None, metavar="N",
                        help="Cap total LLM calls this session (retries count). "
                             "When reached, leaves remaining tasks pending for the next run.")
    parser.add_argument("--mock", action="store_true",
                        help="Skip real LLM_CLI calls; write stub outputs instead")
    parser.add_argument("--no-banner", action="store_true",
                        help="Skip the 5s model banner countdown")
    args = parser.parse_args()

    env = load_env()

    if not (SOURCE_DIR / args.v1).is_dir():
        sys.exit(f"Source not found: source/{args.v1}")
    if not (SOURCE_DIR / args.v2).is_dir():
        sys.exit(f"Source not found: source/{args.v2}")

    if args.mock:
        os.environ["PIPELINE_MOCK"] = "1"
        log("[mock] PIPELINE_MOCK=1 — no real LLM calls this run")

    if args.t is not None:
        if args.t <= 0:
            sys.exit("-t must be a positive integer")
        llm.set_budget(args.t)
        log(f"[budget] session cap: {args.t} LLM calls")

    print_model_banner(env, countdown=0 if args.no_banner or args.mock else 5)

    raw_dir = DIFF_DIR / "raw" / f"{args.v1}-{args.v2}"
    model_root = DIFF_DIR / "models" / env["LLM_MODEL"]
    pair_work = model_root / f"{args.v1}-{args.v2}"
    chunks_dir = pair_work / "chunks"
    analysis_dir = pair_work / "analysis"
    by_domain_dir = pair_work / "analysis-by-domain"
    topics_dir = pair_work / "topics"
    decisions_dir = pair_work / "dedup-decisions"
    deduped_dir = pair_work / "topics-deduped"

    rotated = llm.rotate_failed_markers(pair_work)
    if rotated:
        log(f"[rotate] promoted {rotated} .failed marker(s) to .failed.previous")

    step1_raw_diff(args.v1, args.v2, raw_dir)
    step2_pin_settings(model_root, env)
    manifest = step3_chunk(raw_dir, chunks_dir, env["LLM_CHUNK_SIZE_KB"])

    def stop_on_failure(step_label: str, failed: int) -> None:
        if not failed:
            return
        used, limit = llm.budget_snapshot()
        log("")
        if limit is not None:
            log(f"Budget: {used}/{limit} calls used ({max(0, limit - used)} remaining).")
        else:
            log(f"Calls made: {used}")
        log(f"[{step_label}] STOP — {failed} task(s) failed retries. "
            f"Not proceeding to later steps or writing deliverables.")
        log(f"Inspect *.failed under {pair_work.relative_to(ROOT)}/ and re-run tools/pipeline.py to retry.")
        sys.exit(1)

    s4_failed, s4_pending = step4_analyze(args.v1, args.v2, manifest, chunks_dir, analysis_dir, args.j)
    stop_on_failure("step 4/9", s4_failed)
    step5_concat_domains(manifest, analysis_dir, by_domain_dir)
    s6_failed, s6_pending = step6_topics(args.v1, args.v2, by_domain_dir, topics_dir, args.j)
    stop_on_failure("step 6/9", s6_failed)
    s7_failed, s7_pending = step7_dedup_decide(topics_dir, decisions_dir, args.j)
    stop_on_failure("step 7/9", s7_failed)
    step8_dedup_apply(topics_dir, decisions_dir, deduped_dir)
    step9_assemble(args.v1, args.v2, deduped_dir, env["LLM_MODEL"])

    used, limit = llm.budget_snapshot()
    final_path = RESULTS_DIR / f"{args.v1}-{args.v2}-{env['LLM_MODEL']}.md"

    log("")
    if limit is not None:
        log(f"Budget: {used}/{limit} calls used ({max(0, limit - used)} remaining).")
    else:
        log(f"Calls made: {used}")

    total_pending = s4_pending + s6_pending + s7_pending

    if total_pending:
        log(f"Budget exhausted — {total_pending} task(s) left pending.")
        log("Re-run tools/pipeline.py with the same arguments to continue (fresh budget).")
        sys.exit(0)

    if final_path.exists():
        log(f"Done. Final result: {final_path.relative_to(ROOT)}")
        log(f"Working directory can be removed when satisfied: rm -rf {pair_work.relative_to(ROOT)}/")
    else:
        log("Incomplete run — final result not yet produced. Re-run to continue.")


if __name__ == "__main__":
    main()
