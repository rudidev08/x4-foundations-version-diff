#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_registry import MacroInfo  # noqa: E402
from x4_rules_macro_weapon import resolve  # noqa: E402


class WeaponMacroRuleTest(unittest.TestCase):
    def test_promotes_weapon_macros(self):
        label = resolve(MacroInfo("assets/props/WeaponSystems/macros/weapon_arg_l_beam_01_macro.xml", "weapon_arg_l_beam_01_macro", "weapon"))
        self.assertEqual(label.prefix, "ware:weapon_arg_l_beam_01")

    def test_rejects_turret_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/props/WeaponSystems/macros/weapon_arg_l_beam_01_macro.xml", "weapon_arg_l_beam_01_macro", "turret")))


if __name__ == "__main__":
    unittest.main()
