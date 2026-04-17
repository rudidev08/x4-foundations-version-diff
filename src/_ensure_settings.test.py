#!/usr/bin/env python3
"""
Test _ensure_settings — first run creates settings.json, mismatched re-run aborts.

Run:
    python3 src/_ensure_settings.test.py

CLI shape exercised (called from run.sh in production):
    python3 src/_ensure_settings.py --out DIR --v1 NAME --v2 NAME \\
        --model NAME --llm-cmd CMD --chunk-kb N [--force-split]
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "_ensure_settings.py"


def run(out: Path, *, model="opus-max", chunk_kb=50, llm_cmd="claude --print --model X",
        force_split=False):
    cmd = [sys.executable, str(SCRIPT),
           "--out", str(out), "--v1", "9.00B4", "--v2", "9.00B5",
           "--model", model, "--llm-cmd", llm_cmd, "--chunk-kb", str(chunk_kb)]
    if force_split:
        cmd.append("--force-split")
    return subprocess.run(cmd, capture_output=True, text=True)


class EnsureSettingsTest(unittest.TestCase):
    def test_first_run_writes_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            r = run(out)
            self.assertEqual(r.returncode, 0, r.stderr)

            settings = json.loads((out / "settings.json").read_text())
            self.assertEqual(settings["v1"], "9.00B4")
            self.assertEqual(settings["model_name"], "opus-max")
            self.assertEqual(settings["chunk_kb"], 50)
            self.assertFalse(settings["force_split"])
            self.assertIn("created_at", settings)

    def test_matching_second_run_is_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            run(out)
            original = (out / "settings.json").read_text()
            r2 = run(out)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            # File unchanged (created_at preserved)
            self.assertEqual((out / "settings.json").read_text(), original)

    def test_mismatch_aborts_with_field_detail(self):
        """Changing chunk_kb between runs aborts with the exact mismatch listed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            run(out, chunk_kb=50)
            r2 = run(out, chunk_kb=40)
            self.assertNotEqual(r2.returncode, 0)
            blob = r2.stderr + r2.stdout
            self.assertIn("ABORT", blob)
            self.assertIn("chunk_kb", blob)
            self.assertIn("settings=50", blob)
            self.assertIn("current=40", blob)


if __name__ == "__main__":
    unittest.main()
