#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_registry import MacroInfo  # noqa: E402
from x4_rules_macro_storage import resolve  # noqa: E402


class StorageMacroRuleTest(unittest.TestCase):
    def test_promotes_props_storage_modules(self):
        label = resolve(MacroInfo("assets/props/StorageModules/macros/storage_par_l_miner_liquid_02_a_macro.xml", "storage_par_l_miner_liquid_02_a_macro", "storage"))
        self.assertEqual(label.prefix, "module:storage_par_l_miner_liquid_02_a")

    def test_rejects_unit_attached_storage_modules(self):
        self.assertIsNone(resolve(MacroInfo("assets/units/size_l/macros/storage_ter_l_miner_liquid_01_a_macro.xml", "storage_ter_l_miner_liquid_01_a_macro", "storage")))


if __name__ == "__main__":
    unittest.main()
