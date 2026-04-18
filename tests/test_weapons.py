import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.paths import reset_index
from src.rules import weapons


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'weapons' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'weapons' / 'TEST-2.00'


class WeaponsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = weapons.run(ROOT1, ROOT2)

    def _find(self, entity_key, subsource=None):
        matches = [
            o for o in self.outputs
            if o.extras.get('entity_key') == entity_key
            and o.extras.get('subsource') == subsource
        ]
        return matches

    # Case 1: Added.
    def test_added_new_weapon(self):
        matches = self._find('weapon_arg_s_energy_01_mk1')
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertIn('ARG S Energy Weapon Mk1', out.text)
        self.assertIn('NEW', out.text)
        self.assertIn('energy', out.extras['classifications'])

    # Case 2: Removed.
    def test_removed_weapon(self):
        matches = self._find('weapon_arg_l_heavy_01_mk1')
        # Might have an additional 'bullet' subsource row if the weapon shared
        # a bullet macro whose stats changed — filter to main row only.
        main = [m for m in matches if m.extras.get('subsource') is None]
        self.assertEqual(len(main), 1, msg=[o.text for o in self.outputs])
        out = main[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertIn('ARG L Heavy Weapon Mk1', out.text)
        self.assertIn('REMOVED', out.text)
        self.assertIn('heavy', out.extras['classifications'])

    # Case 3: Modified (ware + macro stats).
    def test_modified_ware_and_macro(self):
        matches = [m for m in self._find('weapon_arg_m_standard_01_mk1')
                   if m.extras.get('subsource') is None]
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('ARG M Standard Weapon Mk1', out.text)
        self.assertIn('standard', out.extras['classifications'])
        # Price changed from 10000 core → 11000 via boron patch.
        self.assertIn('price_min 10000→11000', out.text)
        # Macro stats changed.
        self.assertIn('rotation 100→120', out.text)
        self.assertIn('HP 1000→1200', out.text)

    # Case 4: Lifecycle (deprecation).
    def test_lifecycle_deprecation(self):
        matches = [m for m in self._find('weapon_par_m_energy_01_mk1')
                   if m.extras.get('subsource') is None]
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('DEPRECATED', out.text)

    # Case 5: DLC-sourced weapon.
    def test_dlc_sourced_added(self):
        matches = [m for m in self._find('weapon_bor_m_standard_01_mk1')
                   if m.extras.get('subsource') is None]
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertIn('boron', out.extras['sources'])
        self.assertIn('[boron]', out.text)
        # Boron subtype detected from the DLC macro path.
        self.assertIn('boron', out.extras['classifications'])

    # Case 6: Provenance handoff (core-only → core+boron).
    def test_provenance_handoff(self):
        matches = [m for m in self._find('weapon_arg_m_standard_01_mk1')
                   if m.extras.get('subsource') is None]
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(sorted(out.extras['old_sources']), ['core'])
        self.assertEqual(sorted(out.extras['new_sources']), ['boron', 'core'])
        self.assertIn('core→boron+core', out.text)

    # Case 7: Incomplete (patch failure propagates).
    def test_incomplete_sentinel_emitted(self):
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete']
        self.assertTrue(sentinels, msg=[o.text for o in self.outputs])
        # The split DLC targets a ware that doesn't exist → failure. The
        # affected key is 'weapon_par_m_energy_01_mk1'; that ware's output
        # should be marked incomplete.
        deprecated = [o for o in self.outputs
                      if o.extras.get('entity_key') == 'weapon_par_m_energy_01_mk1'
                      and o.extras.get('subsource') is None]
        self.assertTrue(deprecated)
        self.assertTrue(deprecated[0].extras.get('incomplete'))

    # Case 8: Warning (positional overlap from split+terran).
    def test_warning_emitted(self):
        warnings = [o for o in self.outputs
                    if o.extras.get('kind') == 'warning']
        self.assertTrue(warnings, msg=[o.text for o in self.outputs])
        # At least one warning should be about positional overlap.
        matching = [w for w in warnings if 'positional overlap' in w.text]
        self.assertTrue(matching, msg=[w.text for w in warnings])

    # Case 9: Unchanged weapon emits nothing.
    def test_unchanged_weapon_emits_nothing(self):
        matches = self._find('weapon_arg_m_shotgun_control_01_mk1')
        self.assertEqual(matches, [], msg=[o.text for o in self.outputs])

    # Bullet fan-out: shared bullet macro stat change emits a row per weapon.
    def test_bullet_fanout_emits_per_weapon(self):
        bullet_rows = [o for o in self.outputs
                       if o.extras.get('subsource') == 'bullet']
        # bullet_gen_std_01_mk1_macro is shared between
        # weapon_arg_m_standard_01_mk1 (present both sides) and
        # weapon_arg_l_heavy_01_mk1 (only in v1 — still unioned in).
        keys = sorted({o.extras['entity_key'] for o in bullet_rows})
        self.assertIn('weapon_arg_m_standard_01_mk1', keys)
        self.assertIn('weapon_arg_l_heavy_01_mk1', keys)
        # Damage delta text.
        for o in bullet_rows:
            if o.extras['entity_key'] == 'weapon_arg_m_standard_01_mk1':
                self.assertIn('damage 100→200', o.text)
                self.assertIn('(bullet)', o.text)


if __name__ == '__main__':
    unittest.main()
