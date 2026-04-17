#!/usr/bin/env python3
"""
Test x4_rules_dlc_diff — DLC <diff> selector parsing and interval extraction.

Run:
    python3 src/x4_rules_dlc_diff.test.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_dlc_diff import build_dlc_diff_intervals, is_dlc_diff_file, key_from_sel  # noqa: E402


class DlcDiffRulesTest(unittest.TestCase):
    def test_key_from_sel_uses_innermost_id_predicate(self):
        self.assertEqual(
            key_from_sel("/wares/ware[@id='weapon_arg_l_beam_01']/production/method[@name='default']"),
            "method:default",
        )

    def test_build_dlc_diff_intervals_extracts_operation_and_nested_entity_keys(self):
        xml_text = (
            "<diff>\n"
            "  <add sel=\"/wares/ware[@id='weapon_arg_l_beam_01']/production\">\n"
            "    <ware id=\"weapon_arg_l_beam_01_variant\" />\n"
            "  </add>\n"
            "  <replace sel=\"//ware[@id='shield_gen_m_mk1']/amount\">100</replace>\n"
            "</diff>\n"
        )
        intervals = build_dlc_diff_intervals(xml_text)
        keys = [key for _, _, key in intervals]
        self.assertIn("ware:weapon_arg_l_beam_01", keys)
        self.assertIn("ware:weapon_arg_l_beam_01_variant", keys)
        self.assertIn("ware:shield_gen_m_mk1", keys)

    def test_is_dlc_diff_file_detects_root_tag(self):
        self.assertTrue(is_dlc_diff_file("<diff><add sel=\"/x\"/></diff>"))
        self.assertFalse(is_dlc_diff_file("<wares><ware id=\"x\"/></wares>"))


if __name__ == "__main__":
    unittest.main()
