#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_registry import MacroInfo  # noqa: E402
from x4_rules_macro_turret import resolve  # noqa: E402


class TurretMacroRuleTest(unittest.TestCase):
    def test_promotes_turret_macros(self):
        label = resolve(MacroInfo("assets/props/WeaponSystems/macros/turret_arg_l_laser_01_macro.xml", "turret_arg_l_laser_01_macro", "missileturret"))
        self.assertEqual(label.prefix, "ware:turret_arg_l_laser_01")

    def test_rejects_weapon_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/props/WeaponSystems/macros/turret_arg_l_laser_01_macro.xml", "turret_arg_l_laser_01_macro", "weapon")))


if __name__ == "__main__":
    unittest.main()
