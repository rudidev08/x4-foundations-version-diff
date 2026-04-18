"""Unit tests for the `engines` rule.

Covers the 9-case Wave 1 matrix: added, removed, modified, deprecated,
DLC-sourced, provenance handoff, incomplete (bad xpath), warning (positional
overlap), unchanged (zero outputs).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import change_map
from src.lib import cache
from src.rules import engines


HERE = Path(__file__).resolve().parent


class _BaseEnginesTest(unittest.TestCase):
    """Runs engines.run once against the main TEST-1.00/TEST-2.00 fixtures."""

    @classmethod
    def setUpClass(cls):
        cache.clear()
        cls.root1 = HERE / 'fixtures' / 'engines' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'engines' / 'TEST-2.00'
        cls.changes = change_map.build(cls.root1, cls.root2)
        cls.outputs = engines.run(cls.root1, cls.root2, changes=cls.changes)

    def _find(self, entity_key):
        matches = [o for o in self.outputs
                   if o.extras.get('entity_key') == entity_key]
        return matches


class EnginesRuleTest(_BaseEnginesTest):
    def test_added_engine_arg_s_combat_mk1(self):
        matches = self._find('engine_arg_s_combat_01_mk1')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['classifications'],
                         ['arg', 's', 'combat', 'mk1'])
        self.assertIn('Argon S Combat Engine Mk1', out.text)
        self.assertIn('NEW', out.text)
        self.assertEqual(out.extras['sources'], ['core'])

    def test_removed_engine_arg_l_travel(self):
        matches = self._find('engine_arg_l_travel_01_mk1')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['classifications'],
                         ['arg', 'l', 'travel', 'mk1'])
        self.assertIn('REMOVED', out.text)
        self.assertIn('Argon L Travel Engine Mk1', out.text)

    def test_modified_arg_m_combat(self):
        matches = self._find('engine_arg_m_combat_01_mk1')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        # Ware price_max diff (17000→18500 after DLC replace)
        self.assertIn('price_max 17000→18500', out.text)
        # Macro boost_thrust diff (7.5→8.5)
        self.assertIn('boost_thrust 7.5→8.5', out.text)
        # Macro hull_max diff (150→200)
        self.assertIn('hull_max 150→200', out.text)

    def test_deprecated_par_s_racer(self):
        matches = self._find('engine_par_s_racer_01_mk1')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        # DEPRECATED is prepended to changes — shows up after the ': ' separator
        self.assertIn(': DEPRECATED', out.text)

    def test_dlc_sourced_bor_m_allround(self):
        matches = self._find('engine_bor_m_allround_01_mk1')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertIn('boron', out.extras['sources'])
        self.assertIn('[boron]', out.text)

    def test_provenance_arg_m_combat_has_boron(self):
        matches = self._find('engine_arg_m_combat_01_mk1')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        # Old side shipped only core; new side has boron adding a replace.
        self.assertEqual(sorted(out.extras['old_sources']), ['core'])
        self.assertEqual(sorted(out.extras['new_sources']), ['boron', 'core'])

    def test_unchanged_arg_m_allround_no_output(self):
        matches = self._find('engine_arg_m_allround_01_mk1')
        self.assertEqual(matches, [])


class EnginesIncompleteTest(unittest.TestCase):
    """Case 7: bad xpath in a DLC produces an `incomplete` sentinel."""

    def test_incomplete_from_bad_xpath(self):
        cache.clear()
        root1 = HERE / 'fixtures' / 'engines' / 'TEST-1.00'
        root2 = HERE / 'fixtures' / 'engines' / 'TEST-2.00-incomplete'
        outputs = engines.run(root1, root2,
                              changes=change_map.build(root1, root2))
        incompletes = [o for o in outputs if o.extras.get('kind') == 'incomplete']
        self.assertTrue(incompletes, f'no incomplete sentinel in: {[o.text for o in outputs]}')
        self.assertIn('RULE INCOMPLETE', incompletes[0].text)
        self.assertTrue(incompletes[0].extras.get('incomplete'))


class EnginesWarningTest(unittest.TestCase):
    """Case 8: two DLCs both positional-adding after the same ware → warning."""

    def test_warning_positional_overlap(self):
        cache.clear()
        root1 = HERE / 'fixtures' / 'engines' / 'TEST-1.00'
        root2 = HERE / 'fixtures' / 'engines' / 'TEST-2.00-warning'
        outputs = engines.run(root1, root2,
                              changes=change_map.build(root1, root2))
        warnings = [o for o in outputs if o.extras.get('kind') == 'warning']
        self.assertTrue(warnings, f'no warning in: {[o.text for o in outputs]}')
        self.assertIn('positional overlap', warnings[0].text)


if __name__ == '__main__':
    unittest.main()
