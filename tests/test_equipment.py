import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import change_map
from src.lib import cache
from src.rules import equipment


HERE = Path(__file__).resolve().parent


class EquipmentRuleTest(unittest.TestCase):
    """Wave 1 Task 1.4 — equipment rule test matrix.

    Covers the 9-case Canonical set + the macro-gap warning cases unique to
    this rule (warning-only path, warning + ware-row double-fire).
    """

    @classmethod
    def setUpClass(cls):
        cache.clear()
        cls.root1 = HERE / 'fixtures' / 'equipment' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'equipment' / 'TEST-2.00'
        cls.changes = change_map.build(cls.root1, cls.root2)
        cls.outputs = equipment.run(cls.root1, cls.root2, cls.changes)

    def _find_wares(self, ware_id):
        return [o for o in self.outputs
                if o.extras.get('ware_id') == ware_id
                and o.extras.get('kind') != 'warning']

    def _warnings_for(self, ware_id):
        return [o for o in self.outputs
                if o.extras.get('kind') == 'warning'
                and o.extras.get('ware_id') == ware_id]

    # --- Canonical case 3: modified ---
    def test_software_scanner_modified(self):
        rows = self._find_wares('software_scanner_mk1')
        self.assertEqual(len(rows), 1, msg=f'got {[r.text for r in rows]}')
        row = rows[0]
        self.assertEqual(row.extras['kind'], 'modified')
        self.assertIn('software', row.extras['classifications'])
        self.assertIn('price_min 10000→11000', row.text)
        self.assertIn('price_max 14000→15000', row.text)
        self.assertIn('Object Scanner Mk1', row.text)

    # --- Canonical case 2: removed ---
    def test_hardware_mining_laser_removed(self):
        rows = self._find_wares('hardware_mining_laser_mk1')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.extras['kind'], 'removed')
        self.assertIn('hardware', row.extras['classifications'])
        self.assertIn('REMOVED', row.text)

    # --- Canonical case 1: added; satellite_ routing ---
    def test_satellite_added(self):
        rows = self._find_wares('satellite_mk1')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.extras['kind'], 'added')
        self.assertIn('satellite', row.extras['classifications'])
        self.assertIn('NEW', row.text)

    # --- Canonical case 4-like: spacesuit routing ---
    def test_spacesuit_engine_routed_here(self):
        """engine_gen_spacesuit_01_mk1 MUST route to equipment, not engines.

        Verifies the spacesuit tokenization path: @group=engines but the ware
        lives in equipment because of `spacesuit` id token + personalupgrade
        tag. Classification carries both 'spacesuit' and 'engines_origin' so
        the LLM sees both facets.
        """
        rows = self._find_wares('engine_gen_spacesuit_01_mk1')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.extras['kind'], 'modified')
        self.assertIn('spacesuit', row.extras['classifications'])
        self.assertIn('engines_origin', row.extras['classifications'])

    # --- Canonical case 5: DLC-sourced ---
    def test_spacesuit_weapon_dlc_sourced(self):
        rows = self._find_wares('weapon_gen_spacesuit_laser_01_mk1')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.extras['kind'], 'added')
        self.assertIn('boron', row.extras['sources'])
        self.assertIn('spacesuit', row.extras['classifications'])
        self.assertIn('weapons_origin', row.extras['classifications'])

    # --- Canonical case 6: provenance handoff ---
    def test_provenance_handoff(self):
        """engine_gen_spacesuit_01_mk1 is core-only in TEST-1.00 and
        core+boron in TEST-2.00 (DLC adds an <owner> via <add pos="after">)."""
        rows = self._find_wares('engine_gen_spacesuit_01_mk1')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertNotEqual(row.extras['old_sources'], row.extras['new_sources'])
        self.assertIn('core', row.extras['old_sources'])
        self.assertIn('boron', row.extras['new_sources'])

    # --- Canonical case 7: incomplete sentinel ---
    def test_incomplete_from_bad_xpath(self):
        """ego_dlc_terran ships a `<remove sel="//ware[@id='nonexistent_...']" />`
        which fails with `remove_target_missing`, surfacing an incomplete
        sentinel through `forward_incomplete`.
        """
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete']
        self.assertTrue(sentinels,
                        msg=f'no incomplete sentinel; outputs:\n' +
                        '\n'.join(o.text for o in self.outputs))
        self.assertIn('RULE INCOMPLETE', sentinels[0].text)

    # --- Canonical case 8: warning (positional overlap) ---
    def test_warning_positional_overlap(self):
        """Both boron and split DLCs add an <owner> `pos="after"` at the same
        target on `engine_gen_spacesuit_01_mk1` — classified as
        `positional_overlap` warning (not a failure, since the added children
        carry no id collision).
        """
        warnings = [o for o in self.outputs
                    if o.extras.get('kind') == 'warning'
                    and 'positional overlap' in o.text]
        self.assertTrue(warnings,
                        msg='expected positional_overlap warning; outputs:\n' +
                        '\n'.join(o.text for o in self.outputs))

    # --- Canonical case 9: no-change ware yields no ware rows ---
    def test_unchanged_countermeasures_flare_no_ware_output(self):
        """countermeasures_flare_mk1 is byte-identical in wares.xml between
        TEST-1 and TEST-2. Zero *ware* rows for it; macro-gap warnings may
        still exist (covered by the macro-gap tests).
        """
        rows = self._find_wares('countermeasures_flare_mk1')
        self.assertEqual(rows, [],
                         msg=f'expected zero ware rows; got {[r.text for r in rows]}')

    # --- Macro-gap: warning-only path ---
    def test_macro_gap_warning_only(self):
        """countermeasures_flare_mk1 wares.xml entry is unchanged, but its
        macro file changed (both core and DLC boron). One+ warnings expected.
        """
        warnings = self._warnings_for('countermeasures_flare_mk1')
        self.assertTrue(warnings,
                        msg='expected at least one macro-gap warning for '
                            'countermeasures_flare_mk1; outputs:\n' +
                        '\n'.join(o.text for o in self.outputs))
        # Make sure the warning text names the macro path and ware id.
        for w in warnings:
            self.assertIn('countermeasures_flare_mk1', w.text)
            self.assertTrue(w.extras.get('macro_path'),
                            msg=f'macro_path missing on warning: {w.text}')

    # --- Macro-gap: ware row AND warning both fire for the same ware ---
    def test_macro_gap_warning_plus_ware_row(self):
        """software_scanner_mk1 has BOTH:
        - a wares.xml price delta (emits a modified ware row)
        - a macro file change at `assets/props/Software/macros/software_scanner_mk1_macro.xml`
          (emits a macro-gap warning)
        Both outputs must appear — suppressing the warning when the row
        exists would drop the macro-change signal.
        """
        rows = self._find_wares('software_scanner_mk1')
        warnings = self._warnings_for('software_scanner_mk1')
        self.assertEqual(len(rows), 1,
                         msg=f'expected 1 modified row; got {[r.text for r in rows]}')
        self.assertTrue(warnings,
                        msg='expected >=1 macro-gap warning for '
                            'software_scanner_mk1; outputs:\n' +
                        '\n'.join(o.text for o in self.outputs))

    def test_engines_rule_does_NOT_see_spacesuit_engine(self):
        """Cross-rule verification: the engines rule's `owns(ware, 'engines')`
        predicate must return False for a spacesuit engine, so the engines
        rule emits zero outputs for `engine_gen_spacesuit_01_mk1`.

        If the engines rule module isn't on disk (task order was reshuffled),
        skip — this is a belt-and-braces check, not a blocker.
        """
        try:
            from src.rules import engines
        except ImportError:
            self.skipTest('engines rule not present yet')
        cache.clear()
        outs = engines.run(self.root1, self.root2, self.changes)
        matches = [o for o in outs
                   if o.extras.get('ware_id') == 'engine_gen_spacesuit_01_mk1'
                   or o.extras.get('entity_key') == 'engine_gen_spacesuit_01_mk1']
        self.assertEqual(matches, [],
                         msg='engines rule claimed a spacesuit engine; '
                             'ownership predicate is broken')


if __name__ == '__main__':
    unittest.main()
