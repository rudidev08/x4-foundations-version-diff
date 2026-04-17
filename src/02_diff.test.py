#!/usr/bin/env python3
"""
Test 02_diff — demonstrates the CLI and asserts one artifact per changed file.

Run:
    python3 src/02_diff.test.py

CLI shape exercised:
    python3 src/02_diff.py --v1 DIR --v2 DIR --out DIR

Output artifacts:
    <out>/02_diff/diffs/<path>.diff      (modified) — unified diff with header
    <out>/02_diff/diffs/<path>.added     (added)    — raw V2 file copy
    <out>/02_diff/diffs/<path>.deleted   (deleted)  — raw V1 file copy
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "02_diff.py"


def setup_tree(tmp: Path):
    """Build a V1/V2 pair with one of each status; write the enumeration file."""
    v1, v2, out = tmp / "v1", tmp / "v2", tmp / "art"
    (v1 / "libraries").mkdir(parents=True)
    (v2 / "libraries").mkdir(parents=True)
    # modified: wares.xml
    (v1 / "libraries/wares.xml").write_text("<wares>\n  <ware id='a'/>\n</wares>\n")
    (v2 / "libraries/wares.xml").write_text("<wares>\n  <ware id='a'/>\n  <ware id='b'/>\n</wares>\n")
    # added: factions.xml
    (v2 / "libraries/factions.xml").write_text("faction data\n")
    # deleted: ships.xml
    (v1 / "libraries/ships.xml").write_text("ship data\n")

    enum_file = out / "01_enumerate" / "enumeration.jsonl"
    enum_file.parent.mkdir(parents=True)
    enum_file.write_text("\n".join(json.dumps(e) for e in [
        {"path": "libraries/wares.xml",    "status": "modified",
         "v1_bytes": (v1 / "libraries/wares.xml").stat().st_size,
         "v2_bytes": (v2 / "libraries/wares.xml").stat().st_size},
        {"path": "libraries/factions.xml", "status": "added",
         "v1_bytes": 0,
         "v2_bytes": (v2 / "libraries/factions.xml").stat().st_size},
        {"path": "libraries/ships.xml",    "status": "deleted",
         "v1_bytes": (v1 / "libraries/ships.xml").stat().st_size,
         "v2_bytes": 0},
    ]) + "\n")
    return v1, v2, out


def run_diff(v1, v2, out):
    subprocess.run(
        [sys.executable, str(SCRIPT), "--v1", str(v1), "--v2", str(v2), "--out", str(out)],
        check=True, capture_output=True, text=True,
    )


class DiffTest(unittest.TestCase):
    def test_emits_one_artifact_per_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            v1, v2, out = setup_tree(Path(tmpdir))
            run_diff(v1, v2, out)

            diffs = out / "02_diff" / "diffs"
            diff_file    = diffs / "libraries/wares.xml.diff"
            added_file   = diffs / "libraries/factions.xml.added"
            deleted_file = diffs / "libraries/ships.xml.deleted"

            self.assertTrue(diff_file.exists())
            self.assertTrue(added_file.exists())
            self.assertTrue(deleted_file.exists())

            # Modified: unified diff with header
            diff_text = diff_file.read_text()
            self.assertIn("# Source: libraries/wares.xml", diff_text)
            self.assertIn("# Status: modified", diff_text)
            self.assertIn("# V1 bytes:", diff_text)
            self.assertIn("+  <ware id='b'/>", diff_text)

            # Added: raw V2 content, no header
            self.assertEqual(added_file.read_text(), "faction data\n")

            # Deleted: raw V1 content, no header
            self.assertEqual(deleted_file.read_text(), "ship data\n")

    def test_resumable_skips_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            v1, v2, out = setup_tree(Path(tmpdir))
            target = out / "02_diff" / "diffs" / "libraries/wares.xml.diff"
            target.parent.mkdir(parents=True)
            target.write_text("SENTINEL")
            run_diff(v1, v2, out)
            # Existing artifact untouched
            self.assertEqual(target.read_text(), "SENTINEL")


if __name__ == "__main__":
    unittest.main()
