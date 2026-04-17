#!/usr/bin/env python3
"""
Test x4_rules_macro_registry — resolver ordering and fallback behavior.

Run:
    python3 src/x4_rules_macro_registry.test.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_registry import MacroInfo, resolve_macro_label, resolve_macro_prefix  # noqa: E402


class MacroRegistryTest(unittest.TestCase):
    def test_resolves_promoted_family_prefixes(self):
        info = MacroInfo(
            "extensions/ego_dlc_split/assets/props/Engines/macros/engine_spl_s_combat_01_mk3_macro.xml",
            "engine_spl_s_combat_01_mk3_macro",
            "engine",
        )
        label = resolve_macro_label(info)
        self.assertEqual(label.family, "engine")
        self.assertEqual(label.prefix, "ware:engine_spl_s_combat_01_mk3")

    def test_falls_back_to_macro_prefix_for_ambiguous_storage(self):
        info = MacroInfo(
            "extensions/ego_dlc_terran/assets/units/size_l/macros/storage_ter_l_miner_liquid_01_a_macro.xml",
            "storage_ter_l_miner_liquid_01_a_macro",
            "storage",
        )
        self.assertEqual(resolve_macro_prefix(info), "macro:storage_ter_l_miner_liquid_01_a_macro")

    def test_falls_back_to_macro_prefix_for_unclaimed_family(self):
        info = MacroInfo("assets/misc/macros/spacesuit_player_macro.xml", "spacesuit_player_macro", "object")
        self.assertEqual(resolve_macro_prefix(info), "macro:spacesuit_player_macro")


if __name__ == "__main__":
    unittest.main()
