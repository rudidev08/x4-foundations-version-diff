#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_macro_prod import resolve  # noqa: E402
from x4_rules_macro_registry import MacroInfo  # noqa: E402


class ProdMacroRuleTest(unittest.TestCase):
    def test_promotes_production_macros(self):
        label = resolve(MacroInfo("assets/structures/stations/macros/prod_gen_graphene_macro.xml", "prod_gen_graphene_macro", "production"))
        self.assertEqual(label.prefix, "module:prod_gen_graphene")

    def test_rejects_non_production_classes(self):
        self.assertIsNone(resolve(MacroInfo("assets/structures/stations/macros/prod_gen_graphene_macro.xml", "prod_gen_graphene_macro", "storage")))


if __name__ == "__main__":
    unittest.main()
