#!/usr/bin/env python3
"""
Test _lib — shared helpers used by every pipeline step.

Run:
    python3 src/_lib.test.py

Unlike the numbered steps, _lib is a library (no CLI). These tests import
functions directly to verify:
  - atomic_write_*: writes via .tmp + replace (visible file always complete)
  - load_env:       parses KEY=VALUE files with quoted values + comments
  - resolve_model: maps a profile name to (model_name, llm_cmd, chunk_kb)
  - load_schema_map: reads src/x4_schema_map.generated.json
  - check_or_write_settings: first run writes, mismatch aborts
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _lib import (  # noqa: E402
    DEFAULT_SCHEMA_MAP_FILENAME,
    Progress,
    atomic_write_bytes, atomic_write_text,
    check_or_write_settings,
    load_env, load_schema_map, resolve_model,
)


class AtomicWriteTest(unittest.TestCase):
    def test_text_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "file.txt"
            atomic_write_text(path, "hello\n")
            self.assertEqual(path.read_text(), "hello\n")
            self.assertFalse(path.with_suffix(".txt.tmp").exists())

    def test_bytes_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "blob.bin"
            atomic_write_bytes(path, b"\x00\x01\x02")
            self.assertEqual(path.read_bytes(), b"\x00\x01\x02")


class EnvTest(unittest.TestCase):
    def test_parses_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text(
                "# comment\n"
                "FOO=bar\n"
                'QUOTED="value with spaces"\n'
                "SINGLE='single quoted'\n"
                "EMPTY=\n"
                "INVALID LINE\n"
            )
            env = load_env(env_file)
            self.assertEqual(env["FOO"], "bar")
            self.assertEqual(env["QUOTED"], "value with spaces")
            self.assertEqual(env["SINGLE"], "single quoted")
            self.assertEqual(env["EMPTY"], "")
            self.assertNotIn("INVALID", env)


class ResolveModelTest(unittest.TestCase):
    def test_matches_model_and_returns_triple(self):
        env = {
            "DEFAULT_MODEL": "opus-max",
            "OPUS_MAX_MODEL_NAME": "opus-max",
            "OPUS_MAX_LLM_CMD": "claude --print",
            "OPUS_MAX_CHUNK_KB": "50",
            "HAIKU_MODEL_NAME": "haiku",
            "HAIKU_LLM_CMD": "claude --model haiku",
            "HAIKU_CHUNK_KB": "30",
        }
        p = resolve_model(env, "opus-max")
        self.assertEqual(p.model_name, "opus-max")
        self.assertEqual(p.llm_cmd, "claude --print")
        self.assertEqual(p.chunk_kb, 50)

    def test_missing_model_raises(self):
        with self.assertRaises(KeyError):
            resolve_model({}, "nope")


class SchemaMapTest(unittest.TestCase):
    def test_loads_real_schema_file(self):
        """The checked-in generated schema map should parse cleanly."""
        mp = load_schema_map(Path(__file__).parent / DEFAULT_SCHEMA_MAP_FILENAME)
        self.assertIn("libraries/wares.xml", mp)
        self.assertEqual(mp["libraries/wares.xml"], ("ware", "id"))


class SettingsTest(unittest.TestCase):
    FIELDS = {
        "v1": "9.00B4", "v2": "9.00B5",
        "model_name": "opus-max", "llm_cmd": "cmd",
        "chunk_kb": 50, "force_split": False,
    }

    def test_first_call_writes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            check_or_write_settings(out, self.FIELDS)
            data = json.loads((out / "settings.json").read_text())
            for k, v in self.FIELDS.items():
                self.assertEqual(data[k], v)
            self.assertIn("created_at", data)

    def test_matching_call_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            check_or_write_settings(out, self.FIELDS)
            before = (out / "settings.json").read_text()
            check_or_write_settings(out, self.FIELDS)
            self.assertEqual((out / "settings.json").read_text(), before)

    def test_mismatch_raises_systemexit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            check_or_write_settings(out, self.FIELDS)
            with self.assertRaises(SystemExit) as cm:
                check_or_write_settings(out, {**self.FIELDS, "chunk_kb": 40})
            self.assertIn("chunk_kb", str(cm.exception))


class ProgressTest(unittest.TestCase):
    """Progress emits `[step N/M][K/total] step_name: msg` lines."""

    def _capture(self, env_prefix: str | None = None):
        import io, os
        buf = io.StringIO()
        # Patch X4_STEP_PREFIX for this test only.
        old = os.environ.get("X4_STEP_PREFIX")
        if env_prefix is None:
            os.environ.pop("X4_STEP_PREFIX", None)
        else:
            os.environ["X4_STEP_PREFIX"] = env_prefix
        try:
            p = Progress("02_diff", total=3, out=buf)
            p.tick("libraries/wares.xml")
            p.tick("libraries/ships.xml")
            p.same("← extra detail at [2/3]")
            p.tick("libraries/factions.xml")
            p.log("wrote 3, skipped 0")
        finally:
            if old is None:
                os.environ.pop("X4_STEP_PREFIX", None)
            else:
                os.environ["X4_STEP_PREFIX"] = old
        return buf.getvalue()

    def test_tick_increments_counter(self):
        out = self._capture()
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "[1/3] 02_diff: libraries/wares.xml")
        self.assertEqual(lines[1], "[2/3] 02_diff: libraries/ships.xml")
        # same() reuses the current [K/M]
        self.assertEqual(lines[2], "[2/3] 02_diff: ← extra detail at [2/3]")
        self.assertEqual(lines[3], "[3/3] 02_diff: libraries/factions.xml")
        # log() has no counter bracket
        self.assertEqual(lines[4], "02_diff: wrote 3, skipped 0")

    def test_step_prefix_env_is_prepended(self):
        out = self._capture("[step 2/5]")
        first = out.splitlines()[0]
        self.assertEqual(first, "[step 2/5] [1/3] 02_diff: libraries/wares.xml")


if __name__ == "__main__":
    unittest.main()
