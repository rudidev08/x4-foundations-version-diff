#!/usr/bin/env python3
"""
Test x4_rules_file_filter — X4-specific source inclusion and path normalization.

Run:
    python3 src/x4_rules_file_filter.test.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_file_filter import normalize_source_path, should_include, walk_filtered  # noqa: E402


class FileFilterTest(unittest.TestCase):
    def test_include_rules(self):
        self.assertTrue(should_include("libraries/wares.xml"))
        self.assertTrue(should_include("md/Setup.xml"))
        self.assertTrue(should_include("aiscripts/lib_combat.lua"))
        self.assertTrue(should_include("ui.xml"))
        self.assertTrue(should_include("ui/themes/light/theme.xml"))
        self.assertTrue(should_include("maps/xu_ep2_universe/sectors.xml"))
        self.assertTrue(should_include("t/0001-l044.xml"))
        self.assertTrue(should_include("assets/fx/weaponfx/macros/laser.xml"))
        self.assertTrue(should_include("assets/units/ships/ship_arg_xl_carrier/macros/macro.xml"))

    def test_exclude_rules(self):
        self.assertFalse(should_include("shadergl/foo.xml"))
        self.assertFalse(should_include("cutscenes/intro.xml"))
        self.assertFalse(should_include("index/somefile.xml"))
        self.assertFalse(should_include("libraries/material_library.xml"))
        self.assertFalse(should_include("libraries/sound_library_extra.xml"))
        self.assertFalse(should_include("libraries/sound_env_library.xml"))
        self.assertFalse(should_include("assets/units/ships/ship_arg_xl_carrier/geometry.xml"))
        self.assertFalse(should_include("ui/themes/theme.css"))
        self.assertFalse(should_include("libraries/schema.xsd"))
        self.assertFalse(should_include("libraries/icon.png"))
        self.assertFalse(should_include("t/0001-l049.xml"))

    def test_normalize_source_path_strips_dlc_prefix_and_lowercases(self):
        self.assertEqual(
            normalize_source_path("extensions/ego_dlc_split/assets/props/Engines/macros/Foo.xml"),
            "assets/props/engines/macros/foo.xml",
        )
        self.assertEqual(
            normalize_source_path("extensions/ego_dlc_ventures/ui.xml"),
            "ui.xml",
        )

    def test_walk_yields_filtered_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "libraries").mkdir()
            (root / "libraries/wares.xml").write_text("x")
            (root / "shadergl").mkdir()
            (root / "shadergl/skipped.xml").write_text("x")
            paths = sorted(walk_filtered(root))
            self.assertEqual(paths, ["libraries/wares.xml"])


if __name__ == "__main__":
    unittest.main()
