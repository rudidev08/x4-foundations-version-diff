#!/usr/bin/env python3
"""
Test _scan_schema — the scanner that regenerates src/x4_schema_map.generated.json.

Run:
    python3 src/_scan_schema.test.py

Inline fixtures build tiny XML trees in a temp dir and assert the scanner:
  - records files with a repeating id-bearing direct child,
  - skips files with no repeating direct child (single child, or mixed),
  - skips files whose root tag is <diff>,
  - emits JSON with the expected top-level fields, sorted entries,
  - aborts (non-zero exit, --out untouched) when --source doesn't exist.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _scan_schema import main as scan_main  # noqa: E402


_SCRIPT = Path(__file__).parent / "_scan_schema.py"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ScanBehaviourTest(unittest.TestCase):
    def _run(self, source: Path, out: Path) -> dict:
        rc = scan_main(["--source", str(source), "--out", str(out)])
        self.assertEqual(rc, 0)
        return json.loads(out.read_text())

    def test_repeating_id_child_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "x4_schema_map.generated.json"
            _write(
                src / "libraries" / "wares.xml",
                '<?xml version="1.0"?>\n'
                '<wares>\n'
                '  <ware id="a"><production time="1"/></ware>\n'
                '  <ware id="b"><production time="2"/></ware>\n'
                '  <ware id="c"/>\n'
                '</wares>\n',
            )
            data = self._run(src, out)
            self.assertEqual(len(data["entries"]), 1)
            entry = data["entries"][0]
            self.assertEqual(entry["file"], "libraries/wares.xml")
            self.assertEqual(entry["entity_tag"], "ware")
            self.assertEqual(entry["id_attribute"], "id")

    def test_single_child_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "x4_schema_map.generated.json"
            _write(
                src / "libraries" / "lonely.xml",
                '<?xml version="1.0"?>\n'
                '<root>\n'
                '  <only id="x"><nested/></only>\n'
                '</root>\n',
            )
            data = self._run(src, out)
            self.assertEqual(data["entries"], [])

    def test_ambiguous_picks_tag_with_highest_count(self):
        """Root with two qualifying children → pick the more numerous one.

        Mirrors x4-data/.../region_definitions.xml: <regions> holds both
        <region name=...> (many) and <alias name=...> (fewer). The scanner
        should land on `region`, not skip the file.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "x4_schema_map.generated.json"
            body = ["<regions>"]
            body += [f'  <region name="r{i}"/>' for i in range(10)]
            body += [f'  <alias name="a{i}" ref="r0"/>' for i in range(3)]
            body.append("</regions>")
            _write(src / "libraries" / "region_definitions.xml", "\n".join(body) + "\n")
            data = self._run(src, out)
            self.assertEqual(len(data["entries"]), 1)
            e = data["entries"][0]
            self.assertEqual(e["entity_tag"], "region")
            self.assertEqual(e["id_attribute"], "name")

    def test_excluded_files_are_skipped(self):
        """material_library* and sound_library* are in the pipeline's exclude list.

        The scanner must honour the same list so excluded files don't pollute
        the schema map. If the pipeline won't emit a 02_diff artifact for them,
        they have no business being in the map the chunker consults.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "x4_schema_map.generated.json"
            _write(
                src / "libraries" / "material_library.xml",
                '<materiallibrary>\n'
                '  <collection name="a"><material name="m1"/></collection>\n'
                '  <collection name="b"><material name="m2"/></collection>\n'
                '</materiallibrary>\n',
            )
            _write(
                src / "libraries" / "sound_library_extras.xml",
                '<soundlib>\n'
                '  <sound name="a"/>\n'
                '  <sound name="b"/>\n'
                '</soundlib>\n',
            )
            _write(
                src / "libraries" / "wares.xml",
                '<wares>\n'
                '  <ware id="a"/>\n'
                '  <ware id="b"/>\n'
                '</wares>\n',
            )
            data = self._run(src, out)
            files = [e["file"] for e in data["entries"]]
            self.assertEqual(files, ["libraries/wares.xml"],
                             "excluded basenames must not appear in the map")

    def test_mixed_non_repeating_children_not_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "x4_schema_map.generated.json"
            _write(
                src / "libraries" / "mixed.xml",
                '<?xml version="1.0"?>\n'
                '<root>\n'
                '  <alpha id="1"/>\n'
                '  <beta id="2"/>\n'
                '  <gamma id="3"/>\n'
                '</root>\n',
            )
            data = self._run(src, out)
            self.assertEqual(data["entries"], [])

    def test_diff_root_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "x4_schema_map.generated.json"
            # DLC patch shape: <diff> with repeating <add> children carrying
            # sel=, not id=. Even if the children repeated with a consistent
            # id attr, we skip any <diff>-rooted file.
            _write(
                src / "extensions" / "ego_dlc_toy" / "libraries" / "wares.xml",
                '<?xml version="1.0"?>\n'
                '<diff>\n'
                '  <add sel="/wares"><ware id="x"/></add>\n'
                '  <add sel="/wares"><ware id="y"/></add>\n'
                '</diff>\n',
            )
            data = self._run(src, out)
            self.assertEqual(data["entries"], [])

    def test_dlc_map_with_repeating_named_children_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "x4_schema_map.generated.json"
            _write(
                src / "extensions" / "ego_dlc_toy" / "maps" / "xu_ep2_universe" / "toy_sectors.xml",
                '<macros>\n'
                '  <macro name="sector_a"/>\n'
                '  <macro name="sector_b"/>\n'
                '</macros>\n',
            )
            data = self._run(src, out)
            self.assertEqual(
                data["entries"],
                [
                    {
                        "file": "extensions/ego_dlc_toy/maps/xu_ep2_universe/toy_sectors.xml",
                        "entity_tag": "macro",
                        "id_attribute": "name",
                    }
                ],
            )

    def test_output_shape_and_sorting(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            out = Path(tmp) / "x4_schema_map.generated.json"
            _write(
                src / "libraries" / "b.xml",
                '<root><thing name="a"/><thing name="b"/></root>\n',
            )
            _write(
                src / "libraries" / "a.xml",
                '<root><item id="1"/><item id="2"/></root>\n',
            )
            data = self._run(src, out)
            self.assertIn("last_scanned_at", data)
            self.assertIn("scanned_sources", data)
            self.assertIn("entries", data)
            self.assertEqual(data["scanned_sources"], [str(src)])
            files = [e["file"] for e in data["entries"]]
            self.assertEqual(
                files,
                ["libraries/a.xml", "libraries/b.xml"],
                "entries must be sorted alphabetically by file path",
            )
            # Each entry has exactly the three fields we care about.
            for entry in data["entries"]:
                self.assertEqual(set(entry.keys()), {"file", "entity_tag", "id_attribute"})

    def test_missing_source_exits_nonzero_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x4_schema_map.generated.json"
            missing = Path(tmp) / "does_not_exist"
            proc = subprocess.run(
                [sys.executable, str(_SCRIPT),
                 "--source", str(missing), "--out", str(out)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(out.exists(), "--out must not be touched on abort")
            self.assertIn(str(missing), proc.stderr)


if __name__ == "__main__":
    unittest.main()
