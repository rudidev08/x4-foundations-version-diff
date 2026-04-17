#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_registry import MacroInfo  # noqa: E402
from x4_rules_macro_shield import resolve  # noqa: E402


class ShieldMacroRuleTest(unittest.TestCase):
    def test_promotes_shield_macros(self):
        label = resolve(MacroInfo("assets/props/SurfaceElements/macros/shield_tel_xl_standard_01_mk1_macro.xml", "shield_tel_xl_standard_01_mk1_macro", "shieldgenerator"))
        self.assertEqual(label.prefix, "ware:shield_tel_xl_standard_01_mk1")

    def test_rejects_non_shieldgenerator_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/props/SurfaceElements/macros/shield_fx_macro.xml", "shield_fx_macro", "effectobject")))


if __name__ == "__main__":
    unittest.main()
