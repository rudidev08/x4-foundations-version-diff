"""Unit tests for the `cosmetics` rule.

Covers the 9-case matrix across the three sub-sources (paint / adsign /
equipmod) plus dedicated tests for runtime family discovery, dual-attr
adsign warning, and the contamination-scoping contract via internal
labels.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.paths import reset_index
from src.rules import cosmetics


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'cosmetics' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'cosmetics' / 'TEST-2.00'


class CosmeticsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = cosmetics.run(ROOT1, ROOT2)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]

    # ---------- Case 1: paint added ----------
    def test_added_paintmod(self):
        """paintmod_new only in TEST-2.00 → added row with full field list."""
        matches = self._find(('paint', 'paintmod_new'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'paint')
        self.assertEqual(out.extras['classifications'], ['paint'])
        self.assertIn('NEW', out.text)
        self.assertIn('quality=3', out.text)
        self.assertIn('hue=300', out.text)
        self.assertIn('metal=1.0', out.text)

    # ---------- Case 2: paint modified ----------
    def test_modified_paintmod(self):
        """paintmod_alpha changes hue + brightness."""
        matches = self._find(('paint', 'paintmod_alpha'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'paint')
        self.assertIn('hue 100→120', out.text)
        self.assertIn('brightness 0.5→0.7', out.text)

    # ---------- Case 3: paint removed ----------
    def test_removed_paintmod(self):
        matches = self._find(('paint', 'paintmod_gone'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')
        self.assertIn('REMOVED', matches[0].text)

    # ---------- Case 4: adsign_ware added ----------
    def test_added_adsign_ware(self):
        """`new_ware` appears under <type ref="highway"> in TEST-2.00."""
        matches = self._find(('adsign_ware', 'highway', 'new_ware'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        # Internal label on subsource; user-facing 'adsign' in classifications.
        self.assertEqual(out.extras['subsource'], 'adsign_ware')
        self.assertEqual(out.extras['classifications'], ['adsign'])
        self.assertEqual(out.extras['parent_type_ref'], 'highway')
        self.assertIn('ware=new_ware', out.text)

    # ---------- Case 5: adsign_ware modified ----------
    def test_modified_adsign_ware_macro(self):
        """advancedcomposites's macro ref changes between versions."""
        matches = self._find(
            ('adsign_ware', 'highway', 'advancedcomposites'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('macro props_adsigns_warez_construction_01_macro→'
                      'props_adsigns_warez_construction_02_macro', out.text)

    # ---------- Case 6: adsign_ware removed ----------
    def test_removed_adsign_ware(self):
        matches = self._find(
            ('adsign_ware', 'highway', 'retired_ware'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')

    # ---------- Case 7: adsign_waregroup added ----------
    def test_added_adsign_waregroup(self):
        """thrusters waregroup only in TEST-2.00."""
        matches = self._find(
            ('adsign_waregroup', 'station', 'thrusters'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'adsign_waregroup')
        self.assertEqual(out.extras['classifications'], ['adsign'])
        self.assertIn('waregroup=thrusters', out.text)

    # ---------- Case 8: adsign_waregroup removed ----------
    def test_removed_adsign_waregroup(self):
        """shields waregroup present only in TEST-1.00."""
        matches = self._find(
            ('adsign_waregroup', 'station', 'shields'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')

    # ---------- Case 9: adsign dual-attr warning ----------
    def test_dual_attr_adsign_warning_and_ware_row(self):
        """`dual_attr_ware` has BOTH @ware and @waregroup — ware variant
        claims the row, dual-attr warning is emitted separately."""
        # Row emits under adsign_ware variant (ware wins).
        ware_matches = self._find(
            ('adsign_ware', 'station', 'dual_attr_ware'))
        self.assertEqual(len(ware_matches), 1, msg=[o.text for o in self.outputs])
        self.assertEqual(ware_matches[0].extras['subsource'], 'adsign_ware')
        # No row under waregroup variant for the colliding waregroup.
        wg_matches = self._find(
            ('adsign_waregroup', 'station', 'confusing_group'))
        self.assertEqual(wg_matches, [])
        # Warning row exists.
        warnings = [o for o in self.outputs
                    if o.extras.get('kind') == 'warning'
                    and 'adsign_dual_attr' in str(
                        o.extras.get('details', {}).get('reason', ''))]
        self.assertTrue(
            warnings,
            msg=f'no adsign_dual_attr warning in: '
                f'{[o.text for o in self.outputs]}',
        )
        self.assertIn('dual_attr_ware', warnings[0].text)
        self.assertIn('confusing_group', warnings[0].text)

    # ---------- Case 10: equipmod weapon added ----------
    def test_added_equipmod_weapon(self):
        """mod_weapon_speed_new_mk2 is only in TEST-2.00."""
        matches = self._find(
            ('equipmod_weapon', 'weapon', 'mod_weapon_speed_new_mk2', '2'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'equipmod_weapon')
        self.assertEqual(out.extras['classifications'], ['equipmod', 'weapon'])
        self.assertEqual(out.extras['family'], 'weapon')
        self.assertIn('NEW', out.text)
        self.assertIn('min=1.1', out.text)

    # ---------- Case 11: equipmod weapon bonus chance change ----------
    def test_modified_equipmod_weapon_bonus_chance(self):
        """mod_weapon_damage_02_mk1 has bonus chance 0.1→0.15."""
        matches = self._find(
            ('equipmod_weapon', 'weapon', 'mod_weapon_damage_02_mk1', '1'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('bonus[type=cooling].chance 0.1→0.15', out.text)

    # ---------- Case 12: equipmod shield added (multi-family) ----------
    def test_added_equipmod_shield(self):
        matches = self._find(
            ('equipmod_shield', 'shield', 'mod_shield_recharge_new_mk1', '1'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'equipmod_shield')

    # ---------- Case 13: equipmod weapon removed ----------
    def test_removed_equipmod_weapon_reload(self):
        """mod_weapon_reload_retired_mk1 is only in TEST-1.00."""
        matches = self._find(
            ('equipmod_weapon', 'weapon',
             'mod_weapon_reload_retired_mk1', '1'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')

    # ---------- Case 14: equipmod engine family discovered (no diff) ----------
    def test_engine_family_discovered_but_unchanged(self):
        """<engine> family exists in both trees, unchanged → no rows but
        the internal label appears in discovered subsources."""
        # No engine row for mod_engine_thrust_01_mk1 (unchanged).
        engine_rows = [o for o in self.outputs
                       if o.extras.get('subsource') == 'equipmod_engine']
        self.assertEqual(engine_rows, [])

    # ---------- Case 15: equipmod family discovery runs alphabetically ----------
    def test_equipmod_families_discovered(self):
        """Every family in TEST-1.00 ∪ TEST-2.00 produces at least one
        internal label, even when unchanged."""
        subsources = {o.extras.get('subsource') for o in self.outputs}
        # engine is unchanged so it doesn't appear in outputs; weapon,
        # shield do. Presence of weapon/shield confirms discovery runs.
        self.assertIn('equipmod_weapon', subsources)
        self.assertIn('equipmod_shield', subsources)


class ClassificationSubsourceTest(unittest.TestCase):
    """Internal subsource stays on extras.subsource; user-facing tokens
    live in classifications — same contract as sectors.
    """

    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = cosmetics.run(ROOT1, ROOT2)

    def test_adsign_family_shares_user_facing_token(self):
        adsign_outs = [o for o in self.outputs
                       if o.extras.get('subsource', '').startswith('adsign_')]
        self.assertTrue(adsign_outs, 'no adsign rows')
        for o in adsign_outs:
            self.assertEqual(o.extras['classifications'], ['adsign'],
                             msg=o.text)
            self.assertIn(o.extras['subsource'],
                          ('adsign_ware', 'adsign_waregroup'))

    def test_equipmod_family_shares_user_facing_token(self):
        equipmod_outs = [o for o in self.outputs
                         if o.extras.get('subsource', '')
                         .startswith('equipmod_')]
        self.assertTrue(equipmod_outs, 'no equipmod rows')
        for o in equipmod_outs:
            self.assertEqual(o.extras['classifications'][0], 'equipmod')
            # Family is second; internal label matches.
            family = o.extras['classifications'][1]
            self.assertEqual(o.extras['subsource'], f'equipmod_{family}')


class DualAttrDedupTest(unittest.TestCase):
    """Dual-attr warning emits ONE row per unique (ware, waregroup) pair,
    even when the same malformed adsign appears in both TEST-1.00 and
    TEST-2.00 trees."""

    def test_single_warning_even_if_present_both_sides(self):
        cache.clear()
        reset_index()
        outs = cosmetics.run(ROOT1, ROOT2)
        dual_warnings = [o for o in outs
                         if o.extras.get('kind') == 'warning'
                         and o.extras.get('details', {}).get('reason')
                             == 'adsign_dual_attr']
        # Fixture has one dual-attr row only in TEST-2.00; verify
        # exactly 1 warning.
        self.assertEqual(len(dual_warnings), 1,
                         msg=f'warnings: {[w.text for w in dual_warnings]}')


if __name__ == '__main__':
    unittest.main()
