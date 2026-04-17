#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_registry import MacroInfo  # noqa: E402
from x4_rules_macro_ship import resolve  # noqa: E402


class ShipMacroRuleTest(unittest.TestCase):
    def test_promotes_ship_macros(self):
        label = resolve(MacroInfo("assets/units/size_l/macros/ship_kha_l_destroyer_01_a_macro.xml", "ship_kha_l_destroyer_01_a_macro", "ship_l"))
        self.assertEqual(label.prefix, "ship:ship_kha_l_destroyer_01_a")

    def test_rejects_non_ship_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/units/size_l/macros/ship_video_macro.xml", "ship_video_macro", "destructible")))


if __name__ == "__main__":
    unittest.main()
