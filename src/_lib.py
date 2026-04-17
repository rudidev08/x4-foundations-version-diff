"""
Shared helpers for the X4 changelog pipeline. Stdlib only.

Imported by the five pipeline step scripts (01_enumerate.py ... 05_assemble.py)
and the _ensure_settings.py helper. Not intended to be run directly.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to `path` via a sibling .tmp + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to `path` via a sibling .tmp + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def load_env(env_path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Supports KEY=VALUE, quoted values, # comments."""
    env: dict[str, str] = {}
    path = Path(env_path)
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


@dataclass
class Profile:
    model_name: str
    llm_cmd: str
    chunk_kb: int


def resolve_model(env: dict[str, str], model_name: str) -> Profile:
    """Find the `<KEY>_MODEL_NAME` that matches `model_name`; return its triple."""
    for key, value in env.items():
        if key.endswith("_MODEL_NAME") and value == model_name:
            prefix = key[: -len("_MODEL_NAME")]
            llm_cmd = env.get(f"{prefix}_LLM_CMD")
            chunk_kb = env.get(f"{prefix}_CHUNK_KB")
            if not llm_cmd:
                raise KeyError(f"Model '{model_name}' has no {prefix}_LLM_CMD in env.")
            if not chunk_kb:
                raise KeyError(f"Model '{model_name}' has no {prefix}_CHUNK_KB in env.")
            return Profile(model_name=value, llm_cmd=llm_cmd, chunk_kb=int(chunk_kb))
    raise KeyError(
        f"No model in env matches {model_name!r}. "
        f"Define <KEY>_MODEL_NAME={model_name} plus _LLM_CMD and _CHUNK_KB."
    )


def load_schema_map(path: Path) -> dict[str, tuple[str, str]]:
    """Load a generated schema map JSON file; return {file → (entity_tag, id_attribute)}."""
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    result: dict[str, tuple[str, str]] = {}
    for entry in raw.get("entries", []):
        result[entry["file"]] = (entry["entity_tag"], entry["id_attribute"])
    return result


DEFAULT_SCHEMA_MAP_FILENAME = "x4_schema_map.generated.json"

ALLOWED_PREFIXES_LINE_PREFIX = "# Allowed prefixes JSON:"
CHUNK_HEADER_SEPARATOR_PREFIX = "# ─"


def file_fallback_prefix(source_path: str) -> str:
    return f"file:{source_path}" if source_path else "file:(unknown)"


def count_changed_lines(text: str) -> int:
    return sum(
        1 for line in text.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


def count_hunks(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("@@ -"))


class Progress:
    """Terse per-step progress logger with optional pipeline prefix.

    Thread-safe: `tick` / `at` / `log` serialize output so concurrent workers
    don't interleave characters within a single line.

    Lines look like:

        [step 4/5] [3/88] 04_llm: → libraries__wares.xml__part1of1

    The `[step N/M]` prefix comes from env var `X4_STEP_PREFIX` (set by run.sh).

    Methods:
      tick(msg)    — increment counter, emit "[K/M]" line, return the K.
      at(k, msg)   — emit another line at an explicit K (use the value tick
                     returned so concurrent workers can pair send/receive).
      log(msg)     — emit a summary line with no counter.
    """

    def __init__(self, step_name: str, total: int, out=None):
        import threading
        self.step_name = step_name
        self.total = max(total, 0)
        self.count = 0
        self.prefix = os.environ.get("X4_STEP_PREFIX", "")
        self._out = out if out is not None else sys.stdout
        self._lock = threading.Lock()

    def _emit(self, bracket: str | None, msg: str) -> None:
        parts: list[str] = []
        if self.prefix:
            parts.append(self.prefix)
        if bracket:
            parts.append(bracket)
        parts.append(f"{self.step_name}:")
        parts.append(msg)
        print(" ".join(parts), file=self._out, flush=True)

    def tick(self, msg: str) -> int:
        with self._lock:
            self.count += 1
            n = self.count
            self._emit(f"[{n}/{self.total}]", msg)
            return n

    def at(self, n: int, msg: str) -> None:
        with self._lock:
            self._emit(f"[{n}/{self.total}]", msg)

    def same(self, msg: str) -> None:
        """Sequential-only: emit at the current counter. Use `at` in threads."""
        with self._lock:
            self._emit(f"[{self.count}/{self.total}]", msg)

    def log(self, msg: str) -> None:
        with self._lock:
            self._emit(None, msg)


SETTINGS_FILE = "settings.json"
_SETTINGS_FIELDS = ("v1", "v2", "model_name", "llm_cmd", "chunk_kb", "force_split")


def check_or_write_settings(artifact_dir: Path, current: dict) -> None:
    """Write settings.json on first run; abort on any subsequent mismatch.

    `current` must contain every key in _SETTINGS_FIELDS. A created_at
    timestamp is added on first write and preserved afterward.
    """
    artifact_dir = Path(artifact_dir)
    path = artifact_dir / SETTINGS_FILE
    if path.exists():
        saved = json.loads(path.read_text())
        mismatches = [
            (f, saved.get(f), current.get(f))
            for f in _SETTINGS_FIELDS
            if saved.get(f) != current.get(f)
        ]
        if mismatches:
            lines = [f"[pipeline] ABORT: {path} disagrees with current config."]
            for field, s, c in mismatches:
                lines.append(f"  {field}: settings={s!r}, current={c!r}")
            lines.append("Reset the artifact dir, or restore matching config, before re-running.")
            raise SystemExit("\n".join(lines))
    else:
        record = {**{f: current[f] for f in _SETTINGS_FIELDS},
                  "created_at": datetime.now(timezone.utc).isoformat()}
        atomic_write_text(path, json.dumps(record, indent=2) + "\n")
