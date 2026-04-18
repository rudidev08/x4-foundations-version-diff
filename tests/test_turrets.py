"""Unit tests for the turrets rule.

Covers the 9 Canonical cases plus bullet fan-out and guided classification:

1. Added                    — `turret_arg_s_standard_01_mk1`
2. Removed                  — `turret_arg_l_heavy_01_mk1`
3. Modified                 — `turret_arg_m_standard_01_mk1` (price + hull)
4. Lifecycle (deprecation)  — `turret_par_m_energy_01_mk1`
5. DLC-sourced              — `turret_bor_m_standard_01_mk1` (boron DLC only)
6. Provenance handoff       — `turret_arg_m_standard_01_mk1`
                               (old=[core], new=[boron, core])
7. Incomplete               — DLC with unsupported xpath
8. Warning                  — two DLCs with `pos=after` overlap adds
9. Unchanged                — `turret_gen_m_unchanged_01_mk1` → zero rows
  + bullet-fanout           — `turret_gen_m_shared_0[12]_mk1` both emit `subsource='bullet'`
  + guided-classification   — `turret_arg_s_missile_01_mk1` gets `'guided'`
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import change_map
from src.lib import cache
from src.lib.paths import reset_index
from src.rules import turrets


HERE = Path(__file__).resolve().parent


class TurretsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root1 = HERE / 'fixtures' / 'turrets' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'turrets' / 'TEST-2.00'
        # Reset cached indices so fixture + real-data runs don't bleed.
        reset_index()
        cache.clear()
        cls.changes = change_map.build(cls.root1, cls.root2)
        cls.outputs = turrets.run(cls.root1, cls.root2, cls.changes)

    def _by_key(self, ware_id, kind=None, subsource=None):
        out = []
        for o in self.outputs:
            if o.extras.get('entity_key') != ware_id:
                continue
            if kind is not None and o.extras.get('kind') != kind:
                continue
            if subsource is not None and o.extras.get('subsource') != subsource:
                continue
            out.append(o)
        return out

    # ---------- 9 Canonical cases ----------

    def test_case_1_added(self):
        """Added ware emits kind='added' with new-side sources."""
        matches = self._by_key('turret_arg_s_standard_01_mk1', kind='added')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.tag, 'turrets')
        self.assertEqual(out.extras['entity_key'], 'turret_arg_s_standard_01_mk1')
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['new_sources'], ['core'])
        self.assertIn('standard', out.extras['classifications'])
        self.assertIn('small', out.extras['classifications'])
        self.assertIn('ARG S Standard Turret Mk1', out.text)
        self.assertIn('NEW', out.text)

    def test_case_2_removed(self):
        """Removed ware emits kind='removed' with old-side sources."""
        matches = self._by_key('turret_arg_l_heavy_01_mk1', kind='removed')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['old_sources'], ['core'])
        self.assertIn('heavy', out.extras['classifications'])
        self.assertIn('large', out.extras['classifications'])
        self.assertIn('REMOVED', out.text)
        self.assertIn('ARG L Flak Turret Mk1', out.text)

    def test_case_3_modified(self):
        """Modified ware emits kind='modified' with price + hull diff."""
        matches = self._by_key(
            'turret_arg_m_standard_01_mk1', kind='modified', subsource=None)
        # Should have the main modified row (no subsource), not just a bullet row.
        main = [o for o in matches if not o.extras.get('subsource')]
        self.assertEqual(len(main), 1,
                         f'expected 1 main modified row, got {len(main)}')
        out = main[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('price_min 1000→1300', out.text)
        self.assertIn('hull 1200→1500', out.text)
        self.assertIn('standard', out.extras['classifications'])
        self.assertIn('medium', out.extras['classifications'])

    def test_case_4_deprecation(self):
        """Deprecation-toggle modified row carries DEPRECATED marker."""
        matches = self._by_key('turret_par_m_energy_01_mk1', kind='modified')
        main = [o for o in matches if not o.extras.get('subsource')]
        self.assertEqual(len(main), 1)
        out = main[0]
        self.assertIn('DEPRECATED', out.text)
        self.assertIn('PAR M Plasma Turret Mk1', out.text)

    def test_case_5_dlc_sourced(self):
        """A ware only contributed by a DLC carries the DLC short-name in sources."""
        matches = self._by_key('turret_bor_m_standard_01_mk1', kind='added')
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['new_sources'], ['boron'])
        self.assertIn('[boron]', out.text)
        self.assertIn('BOR M Turret Mk1', out.text)

    def test_case_6_provenance_handoff(self):
        """Core ware gains a DLC contributor in v2 — new_sources includes both."""
        matches = self._by_key(
            'turret_arg_m_standard_01_mk1', kind='modified')
        main = [o for o in matches if not o.extras.get('subsource')]
        self.assertEqual(len(main), 1)
        out = main[0]
        self.assertEqual(sorted(out.extras['old_sources']), ['core'])
        self.assertEqual(sorted(out.extras['new_sources']), ['boron', 'core'])
        self.assertIn('[core→boron+core]', out.text)

    def test_case_7_incomplete(self):
        """Unsupported xpath in a DLC surfaces as an INCOMPLETE sentinel row."""
        incomplete = [o for o in self.outputs
                      if o.extras.get('incomplete') and o.extras.get('kind') == 'incomplete']
        self.assertEqual(len(incomplete), 1)
        self.assertIn('RULE INCOMPLETE', incomplete[0].text)

    def test_case_8_warning_positional_overlap(self):
        """Two DLCs adding after the same sel trigger a positional-overlap warning."""
        warnings = [o for o in self.outputs
                    if o.extras.get('warning') and o.extras.get('kind') == 'warning']
        positional = [w for w in warnings
                      if 'positional overlap' in w.text]
        self.assertEqual(len(positional), 1)
        self.assertIn('overlap1', str(positional[0].extras.get('details', {})))
        self.assertIn('overlap2', str(positional[0].extras.get('details', {})))

    def test_case_9_unchanged_emits_nothing(self):
        """An untouched ware produces zero rule outputs."""
        self.assertEqual(self._by_key('turret_gen_m_unchanged_01_mk1'), [])

    # ---------- Bullet fan-out ----------

    def test_bullet_fanout_emits_per_turret(self):
        """A single bullet macro change emits one row per referencing turret."""
        shared_rows = [
            o for o in self.outputs
            if o.extras.get('entity_key') in (
                'turret_gen_m_shared_01_mk1', 'turret_gen_m_shared_02_mk1')
            and o.extras.get('subsource') == 'bullet'
        ]
        # Exactly one bullet row per shared turret (no duplicates from ware-path).
        keys = sorted(o.extras['entity_key'] for o in shared_rows)
        self.assertEqual(
            keys,
            ['turret_gen_m_shared_01_mk1', 'turret_gen_m_shared_02_mk1'],
        )
        for o in shared_rows:
            self.assertIn('bullet_speed 3000→3500', o.text)
            self.assertIn('[bullet]', o.text)

    # ---------- Guided classification ----------

    def test_guided_classification_appended(self):
        """A turret whose ware carries `missilelauncher` tag gets `guided` appended."""
        matches = self._by_key(
            'turret_arg_s_missile_01_mk1', kind='modified')
        main = [o for o in matches if not o.extras.get('subsource')]
        self.assertEqual(len(main), 1)
        out = main[0]
        self.assertIn('guided', out.extras['classifications'])
        self.assertIn('(missile, small, missile, combat, guided)', out.text)


if __name__ == '__main__':
    unittest.main()
