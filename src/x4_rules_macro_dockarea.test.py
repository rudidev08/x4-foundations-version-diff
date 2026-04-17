#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_dockarea import resolve  # noqa: E402
from x4_rules_macro_registry import MacroInfo  # noqa: E402


class DockareaMacroRuleTest(unittest.TestCase):
    def test_promotes_dockarea_macros(self):
        label = resolve(MacroInfo("assets/structures/stations/macros/dockarea_arg_m_station_01_hightech_macro.xml", "dockarea_arg_m_station_01_hightech_macro", "dockarea"))
        self.assertEqual(label.prefix, "module:dockarea_arg_m_station_01_hightech")

    def test_rejects_non_dockarea_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/structures/stations/macros/dockarea_arg_m_station_01_hightech_macro.xml", "dockarea_arg_m_station_01_hightech_macro", "defencemodule")))


if __name__ == "__main__":
    unittest.main()
