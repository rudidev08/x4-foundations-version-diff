#!/usr/bin/env python3
"""
Test 01_enumerate — demonstrates the CLI and asserts correct classification.

Run:
    python3 src/01_enumerate.test.py

CLI shape exercised:
    python3 src/01_enumerate.py --v1 DIR --v2 DIR --out DIR

Output:
    <--out>/01_enumerate/enumeration.jsonl
    one JSON object per line: {path, status, v1_bytes, v2_bytes}
    status ∈ {"added", "modified", "deleted"}
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "01_enumerate.py"


def run_enumerate(v1: Path, v2: Path, out: Path) -> Path:
    """Invoke 01_enumerate.py as a subprocess — demonstrates the CLI call."""
    subprocess.run(
        [sys.executable, str(SCRIPT), "--v1", str(v1), "--v2", str(v2), "--out", str(out)],
        check=True, capture_output=True, text=True,
    )
    return out / "01_enumerate" / "enumeration.jsonl"


def _entries(jsonl: Path):
    return [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()]


class EnumerateTest(unittest.TestCase):
    def test_detects_modified_added_deleted(self):
        """Modified = changed content; added = V2-only; deleted = V1-only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            v1, v2, out = tmp / "v1", tmp / "v2", tmp / "art"
            # V1 tree
            (v1 / "libraries").mkdir(parents=True)
            (v1 / "libraries/wares.xml").write_text("<wares/>")
            (v1 / "libraries/ships.xml").write_text("will be deleted")
            # V2 tree: wares larger (modified), factions new (added), ships gone (deleted)
            (v2 / "libraries").mkdir(parents=True)
            (v2 / "libraries/wares.xml").write_text("<wares><w/></wares>")
            (v2 / "libraries/factions.xml").write_text("new content")

            entries = _entries(run_enumerate(v1, v2, out))
            by = {e["path"]: e for e in entries}
            self.assertEqual(by["libraries/wares.xml"]["status"], "modified")
            self.assertEqual(by["libraries/factions.xml"]["status"], "added")
            self.assertEqual(by["libraries/ships.xml"]["status"], "deleted")
            # Byte sizes recorded on each side
            self.assertGreater(by["libraries/wares.xml"]["v2_bytes"],
                               by["libraries/wares.xml"]["v1_bytes"])
            self.assertEqual(by["libraries/factions.xml"]["v1_bytes"], 0)
            self.assertEqual(by["libraries/ships.xml"]["v2_bytes"], 0)

    def test_detects_same_size_modified_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            v1, v2, out = tmp / "v1", tmp / "v2", tmp / "art"
            (v1 / "libraries").mkdir(parents=True)
            (v2 / "libraries").mkdir(parents=True)
            v1_text = "<ware id='a'/>\n"
            v2_text = "<ware id='b'/>\n"
            self.assertEqual(len(v1_text), len(v2_text))
            (v1 / "libraries/wares.xml").write_text(v1_text)
            (v2 / "libraries/wares.xml").write_text(v2_text)

            entries = _entries(run_enumerate(v1, v2, out))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["path"], "libraries/wares.xml")
            self.assertEqual(entries[0]["status"], "modified")
            self.assertEqual(entries[0]["v1_bytes"], entries[0]["v2_bytes"])

    def test_resumable_noop_if_output_exists(self):
        """If enumeration.jsonl exists, the step is a no-op — content untouched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            v1, v2, out = tmp / "v1", tmp / "v2", tmp / "art"
            v1.mkdir(); v2.mkdir()
            target = out / "01_enumerate" / "enumeration.jsonl"
            target.parent.mkdir(parents=True)
            target.write_text('{"sentinel": true}\n')
            run_enumerate(v1, v2, out)
            self.assertEqual(target.read_text(), '{"sentinel": true}\n')

    def test_filters_out_excluded_paths(self):
        """Paths under shadergl/, cutscenes/, etc. never appear in the manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            v1, v2, out = tmp / "v1", tmp / "v2", tmp / "art"
            v1.mkdir(); v2.mkdir()
            (v2 / "shadergl").mkdir()
            (v2 / "shadergl/foo.xml").write_text("<x/>")
            (v2 / "libraries").mkdir()
            (v2 / "libraries/wares.xml").write_text("<w/>")

            paths = [e["path"] for e in _entries(run_enumerate(v1, v2, out))]
            self.assertIn("libraries/wares.xml", paths)
            self.assertNotIn("shadergl/foo.xml", paths)

    def test_dlc_libraries_are_included(self):
        """Files under extensions/ego_dlc_*/libraries/ should match the libraries rule."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            v1, v2, out = tmp / "v1", tmp / "v2", tmp / "art"
            v1.mkdir()
            dlc_lib = v2 / "extensions/ego_dlc_split/libraries"
            dlc_lib.mkdir(parents=True)
            (dlc_lib / "wares.xml").write_text("<w/>")

            paths = [e["path"] for e in _entries(run_enumerate(v1, v2, out))]
            self.assertIn("extensions/ego_dlc_split/libraries/wares.xml", paths)


if __name__ == "__main__":
    unittest.main()
