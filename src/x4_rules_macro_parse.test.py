#!/usr/bin/env python3
"""
Test x4_rules_macro_parse — singleton macro parsing with no semantic promotion.

Run:
    python3 src/x4_rules_macro_parse.test.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_parse import parse_singleton_macro  # noqa: E402


class MacroParseTest(unittest.TestCase):
    def test_parses_root_macro_document(self):
        info = parse_singleton_macro(
            "assets/props/SurfaceElements/macros/shield_tel_xl_standard_01_mk1_macro.xml",
            "<macro name='shield_tel_xl_standard_01_mk1_macro' class='shieldgenerator' />",
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.macro_name, "shield_tel_xl_standard_01_mk1_macro")
        self.assertEqual(info.macro_class, "shieldgenerator")

    def test_parses_single_wrapped_macro_document(self):
        info = parse_singleton_macro(
            "assets/props/Engines/macros/engine_spl_s_combat_01_mk3_macro.xml",
            "<macros><macro name='engine_spl_s_combat_01_mk3_macro' class='engine' /></macros>",
        )
        self.assertIsNotNone(info)
        self.assertEqual(info.macro_name, "engine_spl_s_combat_01_mk3_macro")

    def test_rejects_multi_macro_documents(self):
        info = parse_singleton_macro(
            "assets/props/Engines/macros/multi.xml",
            "<macros><macro name='a' class='engine' /><macro name='b' class='engine' /></macros>",
        )
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
