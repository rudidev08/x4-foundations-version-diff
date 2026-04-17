#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_buildmodule import resolve  # noqa: E402
from x4_rules_macro_registry import MacroInfo  # noqa: E402


class BuildmoduleMacroRuleTest(unittest.TestCase):
    def test_promotes_buildmodule_macros(self):
        label = resolve(MacroInfo("assets/structures/stations/macros/buildmodule_gen_carrier_macro.xml", "buildmodule_gen_carrier_macro", "buildmodule"))
        self.assertEqual(label.prefix, "module:buildmodule_gen_carrier")

    def test_rejects_other_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/structures/stations/macros/buildmodule_gen_carrier_macro.xml", "buildmodule_gen_carrier_macro", "production")))


if __name__ == "__main__":
    unittest.main()
