import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.rules import wares


HERE = Path(__file__).resolve().parent


class WaresRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root1 = HERE / 'fixtures' / 'wares' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'wares' / 'TEST-2.00'

    def setUp(self):
        cache.clear()
        self.outputs = wares.run(self.root1, self.root2)

    def _find(self, entity_key: str):
        matches = [o for o in self.outputs if o.extras.get('entity_key') == entity_key]
        self.assertEqual(len(matches), 1,
                         msg=f'expected 1 match for {entity_key}, got {len(matches)}')
        return matches[0]

    # Case 1 — added entity
    def test_new_resource_added(self):
        out = self._find('ware_new_resource')
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['classifications'], ['food', 'container', 'economy'])
        self.assertIn('NEW', out.text)
        self.assertIn('Test New Resource', out.text)
        self.assertIn('(food, container, economy)', out.text)
        self.assertIn('[core]', out.text)
        self.assertEqual(out.extras['new_sources'], ['core'])

    # Case 2 — removed entity
    def test_obsolete_widget_removed(self):
        out = self._find('test_ware_obsolete_widget')
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertIn('REMOVED', out.text)
        self.assertIn('Test Obsolete Widget', out.text)
        self.assertEqual(out.extras['old_sources'], ['core'])

    # Case 3 — modified ware (price + production field diffs + owner diff)
    def test_silicon_modified_price_and_production(self):
        out = self._find('test_ware_silicon')
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('price_min 100→120', out.text)
        self.assertIn('price_avg 125→140', out.text)
        self.assertIn('price_max 150→160', out.text)
        self.assertIn('production[method=default] time 100→110', out.text)
        self.assertIn('production[method=default] amount 50→55', out.text)
        self.assertIn('owner_factions added={teladi}', out.text)

    # Case 4 — deprecation toggle
    def test_ore_deprecated(self):
        out = self._find('test_ware_ore')
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('DEPRECATED', out.text)
        # DEPRECATED must be prepended (first change in the list).
        self.assertTrue(out.text.split(': ', 1)[1].startswith('DEPRECATED'),
                        msg=f'DEPRECATED should be first change: {out.text}')

    # Case 5 — DLC-sourced
    def test_boron_exclusive_dlc_sourced(self):
        out = self._find('ware_boron_exclusive')
        self.assertEqual(out.extras['kind'], 'added')
        self.assertIn('boron', out.extras['new_sources'])
        self.assertNotIn('core', out.extras['new_sources'])
        self.assertIn('[boron]', out.text)
        self.assertIn('Test Boron Exclusive', out.text)

    # Case 6 — provenance handoff (core-only v1 → core+DLC v2)
    def test_silicon_provenance_handoff(self):
        out = self._find('test_ware_silicon')
        self.assertEqual(out.extras['old_sources'], ['core'])
        self.assertEqual(sorted(out.extras['new_sources']), ['boron', 'core'])
        self.assertIn('[core→boron+core]', out.text)

    # Case 7 (extra) — ware with multiple production methods
    def test_silicon_multi_method_production_diff(self):
        out = self._find('test_ware_silicon')
        # v1 had method=default AND method=boron; v2 dropped method=boron,
        # modified method=default. Both method-level labels must appear.
        self.assertIn('production[method=boron] removed', out.text)
        self.assertIn('production[method=default] time 100→110', out.text)

    # Case 7 — incomplete sentinel from bad-xpath DLC
    def test_incomplete_from_bad_xpath(self):
        sentinels = [o for o in self.outputs if o.extras.get('kind') == 'incomplete']
        self.assertEqual(len(sentinels), 1,
                         msg=f'expected 1 incomplete sentinel, got {len(sentinels)}')
        self.assertIn('RULE INCOMPLETE', sentinels[0].text)
        self.assertTrue(sentinels[0].extras.get('incomplete'))

    # Case 8 — positional overlap warning from two DLCs
    def test_warning_positional_overlap(self):
        warnings = [o for o in self.outputs if o.extras.get('kind') == 'warning']
        # At least one warning for positional overlap.
        overlaps = [w for w in warnings if 'positional overlap' in w.text]
        self.assertTrue(overlaps,
                        msg=f'expected positional overlap warning, got {[w.text for w in warnings]}')

    # Case 9 — unchanged ware emits nothing
    def test_energycells_unchanged_no_output(self):
        matches = [o for o in self.outputs if o.extras.get('entity_key') == 'test_ware_energy']
        self.assertEqual(matches, [],
                         msg=f'unchanged ware should emit no output; got {[o.text for o in matches]}')


if __name__ == '__main__':
    unittest.main()
