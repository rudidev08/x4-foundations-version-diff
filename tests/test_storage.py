"""Unit tests for the `storage` rule.

Covers the macro-driven matrix: added / removed / modified / DLC-sourced,
plus the parent-ship reverse-index behavior (zero / singular / plural).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import change_map
from src.lib import cache
from src.rules import storage


HERE = Path(__file__).resolve().parent


class _BaseStorageTest(unittest.TestCase):
    """Runs storage.run once against fixture TEST-1.00/TEST-2.00 trees."""

    @classmethod
    def setUpClass(cls):
        cache.clear()
        cls.root1 = HERE / 'fixtures' / 'storage' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'storage' / 'TEST-2.00'
        cls.changes = change_map.build(cls.root1, cls.root2)
        cls.outputs = storage.run(cls.root1, cls.root2, changes=cls.changes)

    def _find(self, macro_name):
        matches = [o for o in self.outputs
                   if o.extras.get('macro') == macro_name]
        return matches


class StorageRuleTest(_BaseStorageTest):

    def test_modified_arg_m_container_plural_parents(self):
        """Case 1: modified storage with 2+ ship refs → plural parent_ships."""
        matches = self._find('storage_arg_m_container_01_macro')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['classifications'], ['container'])
        self.assertIn('cargo_max 5000→6500', out.text)
        # Plural form only; singular omitted
        self.assertNotIn('parent_ship', out.extras)
        self.assertEqual(out.extras['parent_ships'], [
            'ship_arg_l_transport_01_macro',
            'ship_arg_m_fighter_01_macro',
        ])

    def test_removed_arg_s_liquid_zero_parents(self):
        """Case 2 + 3: removed storage, no ship refs → both parent keys absent.

        Also covers the `removed` lifecycle: looks up in old-side index.
        """
        matches = self._find('storage_arg_s_liquid_01_macro')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['classifications'], ['liquid'])
        self.assertIn('REMOVED', out.text)
        self.assertNotIn('parent_ship', out.extras)
        self.assertNotIn('parent_ships', out.extras)

    def test_added_arg_xl_container_singular_parent(self):
        """Case 4: added storage with one ship ref in new tree → parent_ship."""
        matches = self._find('storage_arg_xl_container_01_macro')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['classifications'], ['container'])
        self.assertIn('NEW', out.text)
        self.assertEqual(out.extras['parent_ship'],
                         'ship_arg_l_transport_02_macro')
        self.assertNotIn('parent_ships', out.extras)

    def test_dlc_added_bor_m_solid(self):
        """Case 5 + 9: DLC-sourced add; classifications includes `solid`."""
        matches = self._find('storage_bor_m_solid_01_macro')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['classifications'], ['solid'])
        self.assertEqual(out.extras['source'], 'boron')
        self.assertIn('[boron]', out.text)
        self.assertIn('NEW', out.text)
        self.assertNotIn('parent_ship', out.extras)
        self.assertNotIn('parent_ships', out.extras)

    def test_classification_container(self):
        """Case 5 (part 2): container classification preserved for modified."""
        matches = self._find('storage_arg_m_container_01_macro')
        self.assertEqual(matches[0].extras['classifications'], ['container'])
        self.assertIn('(container)', matches[0].text)

    def test_classification_liquid(self):
        """Case 5 (part 3): liquid classification preserved for removed."""
        matches = self._find('storage_arg_s_liquid_01_macro')
        self.assertEqual(matches[0].extras['classifications'], ['liquid'])
        self.assertIn('(liquid)', matches[0].text)

    def test_unchanged_arg_l_container_02_no_output(self):
        """Case 6: unchanged storage emits no row."""
        matches = self._find('storage_arg_l_container_02_macro')
        self.assertEqual(matches, [])

    def test_all_outputs_have_expected_shape(self):
        """Smoke check: every output carries tag='storage', macro, entity_key,
        kind, source, and rendered text including the macro name."""
        self.assertTrue(self.outputs)
        for out in self.outputs:
            self.assertEqual(out.tag, 'storage')
            self.assertIn('macro', out.extras)
            self.assertIn('entity_key', out.extras)
            self.assertIn('kind', out.extras)
            self.assertIn('source', out.extras)
            self.assertIn(out.extras['macro'], out.text)
            self.assertTrue(out.text.startswith('[storage]'))

    def test_output_count_matches_expected(self):
        """Exactly 4 outputs: modified, removed, added (XL), DLC-added (boron)."""
        self.assertEqual(
            len(self.outputs), 4,
            msg=f'expected 4 outputs, got: {[o.text for o in self.outputs]}',
        )


if __name__ == '__main__':
    unittest.main()
