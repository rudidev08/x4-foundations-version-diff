#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_engine import resolve  # noqa: E402
from x4_rules_macro_registry import MacroInfo  # noqa: E402


class EngineMacroRuleTest(unittest.TestCase):
    def test_promotes_engine_macros(self):
        label = resolve(MacroInfo("extensions/ego_dlc_split/assets/props/Engines/macros/engine_spl_s_combat_01_mk3_macro.xml", "engine_spl_s_combat_01_mk3_macro", "engine"))
        self.assertEqual(label.prefix, "ware:engine_spl_s_combat_01_mk3")

    def test_rejects_non_engine_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/props/Engines/macros/engine_video_macro.xml", "engine_video_macro", "destructible")))


if __name__ == "__main__":
    unittest.main()
