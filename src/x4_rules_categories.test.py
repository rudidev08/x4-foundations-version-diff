#!/usr/bin/env python3
"""
Test x4_rules_categories — promoted-prefix and file-path categorization rules.

Run:
    python3 src/x4_rules_categories.test.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_categories import OTHER, categorize, category_order, normalize_source_path  # noqa: E402


class CategoryRulesTest(unittest.TestCase):
    def test_promoted_prefixes_map_to_expected_categories(self):
        self.assertEqual(categorize("ship:ship_arg_xl_carrier_01_a", ""), "Ships, Stations & Modules")
        self.assertEqual(categorize("module:storage_par_l_miner_liquid_02_a", ""), "Ships, Stations & Modules")
        self.assertEqual(categorize("ware:engine_spl_s_combat_01_mk3", ""), "Weapons & Equipment")
        self.assertEqual(categorize("effect:ref_missile_explosion_medium_01", ""), "Weapons & Equipment")
        self.assertEqual(categorize("influencelist:ion_disrupt_m", ""), "Weapons & Equipment")
        self.assertEqual(categorize("job:scaleplate_smuggler_s", ""), "Jobs & Spawns")
        self.assertEqual(categorize("dataset:Cluster_100_Sector001_macro", ""), "Map & Sectors")
        self.assertEqual(categorize("group:asteroid_ore_s", ""), "Map & Sectors")
        self.assertEqual(categorize("aiscript:order.move.recon", ""), "Missions & Scripts")

    def test_gamestart_station_and_plan_prefixes_override_generic_station_buckets(self):
        self.assertEqual(
            categorize(
                "station:x4ep1_gamestart_scientist_hq_coh",
                "extensions/ego_dlc_terran/libraries/god.xml",
            ),
            "Game Starts",
        )
        self.assertEqual(
            categorize("plan:x4ep1_gamestart_trade_playerfactory", "libraries/constructionplans.xml"),
            "Game Starts",
        )

    def test_file_fallback_normalizes_dlc_paths(self):
        source = "extensions/ego_dlc_split/assets/props/Engines/macros/engine_spl_s_combat_01_mk3_macro.xml"
        self.assertEqual(normalize_source_path(source), "assets/props/engines/macros/engine_spl_s_combat_01_mk3_macro.xml")
        self.assertEqual(categorize("file:" + source, source), "Weapons & Equipment")

    def test_file_fallback_routes_weapon_fx_macros_to_weapons_and_equipment(self):
        source = "extensions/ego_dlc_split/assets/fx/weaponFx/macros/bullet_kha_m_beam_01_macro.xml"
        self.assertEqual(normalize_source_path(source), "assets/fx/weaponfx/macros/bullet_kha_m_beam_01_macro.xml")
        self.assertEqual(categorize("file:" + source, source), "Weapons & Equipment")

    def test_file_fallback_routes_weak_model_collapses_to_stable_sections(self):
        self.assertEqual(categorize("file:libraries/jobs.xml", "libraries/jobs.xml"), "Jobs & Spawns")
        self.assertEqual(categorize("file:libraries/mapdefaults.xml", "libraries/mapdefaults.xml"), "Map & Sectors")
        self.assertEqual(
            categorize("file:libraries/regionobjectgroups.xml", "libraries/regionobjectgroups.xml"),
            "Map & Sectors",
        )
        self.assertEqual(categorize("file:libraries/aicompat.xml", "libraries/aicompat.xml"), "Missions & Scripts")
        self.assertEqual(
            categorize("file:md/gs_trade.xml", "md/gs_trade.xml"),
            "Game Starts",
        )
        self.assertEqual(
            categorize("file:t/0001-l044.xml", "t/0001-l044.xml"),
            "UI",
        )

    def test_unknown_prefix_falls_back_to_other(self):
        self.assertEqual(categorize("mystery:thing", "misc/file.txt"), OTHER)

    def test_category_order_ends_with_other(self):
        self.assertEqual(category_order()[-1], OTHER)
        self.assertIn("Jobs & Spawns", category_order())
        self.assertIn("Game Starts", category_order())


if __name__ == "__main__":
    unittest.main()
