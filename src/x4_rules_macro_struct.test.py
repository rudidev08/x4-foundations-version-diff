#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_registry import MacroInfo  # noqa: E402
from x4_rules_macro_struct import resolve  # noqa: E402


class StructMacroRuleTest(unittest.TestCase):
    def test_promotes_struct_macros(self):
        label = resolve(MacroInfo("assets/structures/stations/macros/struct_arg_vertical_01_macro.xml", "struct_arg_vertical_01_macro", "connectionmodule"))
        self.assertEqual(label.prefix, "module:struct_arg_vertical_01")

    def test_rejects_other_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/structures/stations/macros/struct_arg_vertical_01_macro.xml", "struct_arg_vertical_01_macro", "dockarea")))


if __name__ == "__main__":
    unittest.main()
