#!/usr/bin/env python3
"""
Test x4_rules_schema_scan — X4-specific scan roots and child qualification.

Run:
    python3 src/x4_rules_schema_scan.test.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_schema_scan import choose_repeating_child_entity, iter_scan_roots  # noqa: E402


class SchemaScanRulesTest(unittest.TestCase):
    def test_iter_scan_roots_includes_vanilla_and_dlc_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir)
            (source / "libraries").mkdir()
            (source / "maps").mkdir()
            (source / "extensions" / "ego_dlc_split" / "libraries").mkdir(parents=True)
            (source / "extensions" / "ego_dlc_split" / "maps").mkdir(parents=True)
            roots = sorted((path.relative_to(source).as_posix(), rel) for path, rel in iter_scan_roots(source))
            self.assertEqual(
                roots,
                [
                    ("extensions/ego_dlc_split/libraries", "extensions/ego_dlc_split/libraries"),
                    ("extensions/ego_dlc_split/maps", "extensions/ego_dlc_split/maps"),
                    ("libraries", "libraries"),
                    ("maps", "maps"),
                ],
            )

    def test_choose_repeating_child_entity_prefers_highest_count_then_attr_order(self):
        match = choose_repeating_child_entity(
            "regions",
            {"alias": 3, "region": 10},
            {
                "alias": {"name": 3},
                "region": {"name": 10, "macro": 10},
            },
        )
        self.assertEqual(match, ("region", "name"))

    def test_choose_repeating_child_entity_skips_diff_roots(self):
        self.assertIsNone(
            choose_repeating_child_entity(
                "diff",
                {"add": 5},
                {"add": {"id": 5}},
            )
        )


if __name__ == "__main__":
    unittest.main()
