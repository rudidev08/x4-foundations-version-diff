"""Unit tests for the `ships` rule.

Covers the canonical 9-case matrix across the three sub-sources
(macro / ware / role): added, removed, modified, deprecated/lifecycle,
DLC-sourced, provenance handoff, incomplete (parse error), warning,
unchanged.

Case 6 (multi-sub-source provenance) is demonstrated by
`ship_arg_m_fighter_01` — the same ship emits a `macro` row, a `ware`
row, and a `role` row in one run (`argon_fighter_m` in ships.xml).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.paths import reset_index
from src.rules import ships


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'ships' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'ships' / 'TEST-2.00'


class ShipsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = ships.run(ROOT1, ROOT2)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]

    # ---------- Case 1: Added (macro sub-source) ----------
    def test_added_dlc_macro(self):
        """Boron M fighter macro is new in TEST-2.00 under the boron DLC."""
        matches = self._find(('macro', 'ship_bor_m_fighter_01_macro'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'macro')
        self.assertEqual(out.extras['source'], 'boron')
        self.assertIn('NEW', out.text)
        self.assertIn('[boron]', out.text)
        self.assertIn('Boron M Fighter', out.text)
        self.assertIn('ship_m', out.extras['classifications'])
        self.assertIn('fighter', out.extras['classifications'])

    def test_added_l_transport_core(self):
        """New core L transport macro file in TEST-2.00."""
        matches = self._find(('macro', 'ship_arg_l_transport_01_macro'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'macro')
        self.assertEqual(out.extras['source'], 'core')
        self.assertIn('Argon L Transport', out.text)
        self.assertIn('[core]', out.text)

    # ---------- Case 2: Removed (ware + macro sub-sources) ----------
    def test_removed_ware(self):
        """ship_arg_s_fighter_01_a is in TEST-1.00 only."""
        matches = self._find(('ware', 'ship_arg_s_fighter_01_a'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'ware')
        self.assertIn('REMOVED', out.text)
        self.assertIn('Argon S Fighter', out.text)

    def test_removed_macro(self):
        """ship_arg_s_fighter_01_macro is in TEST-1.00 only."""
        matches = self._find(('macro', 'ship_arg_s_fighter_01_macro'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'macro')
        self.assertIn('REMOVED', out.text)

    # ---------- Case 3: Modified (macro) ----------
    def test_modified_macro_stats(self):
        """ship_arg_m_fighter_01_macro has hull + jerk + storage changes."""
        matches = self._find(('macro', 'ship_arg_m_fighter_01_macro'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'macro')
        self.assertIn('hull_max 10000→12000', out.text)
        self.assertIn('jerk_forward 0.5→0.6', out.text)
        self.assertIn('storage_missile 8→10', out.text)

    # ---------- Case 6: Multi-sub-source provenance (same ship, 3 rows) ----------
    def test_provenance_macro_ware_role_all_surface(self):
        """ship_arg_m_fighter_01 surfaces in macro, ware, and role subsources."""
        macro_row = self._find(('macro', 'ship_arg_m_fighter_01_macro'))
        ware_row = self._find(('ware', 'ship_arg_m_fighter_01_a'))
        role_row = self._find(('role', 'argon_fighter_m'))
        self.assertEqual(len(macro_row), 1)
        self.assertEqual(len(ware_row), 1)
        self.assertEqual(len(role_row), 1)
        self.assertEqual(macro_row[0].extras['subsource'], 'macro')
        self.assertEqual(ware_row[0].extras['subsource'], 'ware')
        self.assertEqual(role_row[0].extras['subsource'], 'role')

    def test_ware_provenance_handoff(self):
        """Boron DLC replaces price_max → old_sources=[core], new_sources=[boron,core]."""
        matches = self._find(('ware', 'ship_arg_m_fighter_01_a'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(sorted(out.extras['old_sources']), ['core'])
        self.assertEqual(sorted(out.extras['new_sources']), ['boron', 'core'])
        self.assertIn('[core→boron+core]', out.text)
        # Price min changed in core; max additionally changed via boron DLC.
        self.assertIn('price_min 500000→520000', out.text)
        self.assertIn('price_max 620000→650000', out.text)
        # Owner faction set changed.
        self.assertIn('owner_factions added={paranid}', out.text)

    # ---------- Case 4: Role modification (lifecycle-like) ----------
    def test_role_modified(self):
        """argon_fighter_m has category tags + pilot tags changes."""
        matches = self._find(('role', 'argon_fighter_m'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('category_tags', out.text)
        self.assertIn('pilot_tags', out.text)
        self.assertIn('category_faction', out.text)
        # classifications come from NEW side <category @tags> + size
        self.assertIn('fighter', out.extras['classifications'])
        self.assertIn('escort', out.extras['classifications'])
        self.assertIn('ship_m', out.extras['classifications'])

    def test_role_added(self):
        """argon_miner_s is new in TEST-2.00."""
        matches = self._find(('role', 'argon_miner_s'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'role')
        self.assertIn('NEW', out.text)
        self.assertIn('miner', out.extras['classifications'])

    def test_role_removed(self):
        """argon_fighter_s was in TEST-1.00 only."""
        matches = self._find(('role', 'argon_fighter_s'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertIn('REMOVED', out.text)

    # ---------- Case 5: DLC-sourced ware ----------
    def test_dlc_ware_added(self):
        """ship_bor_m_fighter_01_a added via boron DLC ware insert."""
        matches = self._find(('ware', 'ship_bor_m_fighter_01_a'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'ware')
        self.assertIn('boron', out.extras['new_sources'])
        self.assertIn('[boron]', out.text)
        self.assertIn('Boron M Fighter', out.text)

    def test_drone_ware_added(self):
        """Drone ware (group='drones') is picked up by the ware key_fn."""
        matches = self._find(('ware', 'ship_gen_xs_drone_01_a'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'ware')

    # ---------- Case 7: Parse-error incomplete sentinel ----------
    def test_malformed_macro_emits_incomplete(self):
        """ship_tel_m_fighter_01_macro in TEST-2.00 has malformed XML."""
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete']
        self.assertTrue(sentinels,
                        msg=f'no incomplete sentinel in: '
                            f'{[o.text for o in self.outputs]}')
        # At least one sentinel mentions the macro or ware subsource.
        macro_sentinels = [s for s in sentinels
                           if s.extras.get('subsource') == 'macro']
        self.assertTrue(macro_sentinels,
                        msg=f'no macro sentinel: {[s.text for s in sentinels]}')
        self.assertIn('RULE INCOMPLETE', macro_sentinels[0].text)
        self.assertTrue(macro_sentinels[0].extras.get('incomplete'))

    def test_malformed_macro_ware_side_contaminated(self):
        """The TEL ware points at the malformed macro; the ware row must be
        flagged incomplete, and a ware-subsource sentinel emitted."""
        ware_row = self._find(('ware', 'ship_tel_m_fighter_01_a'))
        self.assertEqual(len(ware_row), 1)
        out = ware_row[0]
        self.assertTrue(out.extras.get('incomplete'),
                        msg=f'TEL ware row not marked incomplete: {out.text}')
        ware_sentinels = [o for o in self.outputs
                          if o.extras.get('kind') == 'incomplete'
                          and o.extras.get('subsource') == 'ware']
        self.assertTrue(ware_sentinels,
                        msg='ware-sub-source sentinel missing')

    def test_run_does_not_crash_on_malformed(self):
        """Sanity: the rule ran to completion producing a non-empty output
        list even though one macro was malformed."""
        self.assertTrue(self.outputs)

    # ---------- Case 9: Unchanged ----------
    def test_unchanged_macro_emits_nothing(self):
        matches = self._find(('macro', 'ship_arg_m_transport_01_macro'))
        self.assertEqual(matches, [])

    def test_unchanged_ware_emits_nothing(self):
        matches = self._find(('ware', 'ship_arg_m_transport_01_a'))
        self.assertEqual(matches, [])

    def test_unchanged_role_emits_nothing(self):
        matches = self._find(('role', 'argon_transport_m'))
        self.assertEqual(matches, [])


class ShipsWareKeyFnTest(unittest.TestCase):
    """The ware key_fn filter is load-bearing — verify the three branches."""

    def test_transport_ship_accepted(self):
        import xml.etree.ElementTree as ET
        e = ET.fromstring('<ware id="x" transport="ship" />')
        self.assertEqual(ships._ware_key_fn(e), 'x')

    def test_ship_in_tags_accepted(self):
        import xml.etree.ElementTree as ET
        e = ET.fromstring('<ware id="x" transport="container" tags="ship bonus" />')
        self.assertEqual(ships._ware_key_fn(e), 'x')

    def test_group_drones_accepted(self):
        import xml.etree.ElementTree as ET
        e = ET.fromstring('<ware id="x" group="drones" transport="equipment" />')
        self.assertEqual(ships._ware_key_fn(e), 'x')

    def test_non_ship_rejected(self):
        import xml.etree.ElementTree as ET
        e = ET.fromstring('<ware id="x" transport="container" tags="economy" />')
        self.assertIsNone(ships._ware_key_fn(e))


if __name__ == '__main__':
    unittest.main()
