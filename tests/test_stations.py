"""Unit tests for the `stations` rule.

Covers the canonical 9-case matrix cumulatively across five sub-sources
(station, stationgroup, module, modulegroup, constructionplan) plus the
cross-entity ref-graph validation:

- Typed constructionplan refs split (module vs modulegroup vs unresolved).
- Dangling station.group_ref → warning.
- Dangling modulegroup.select @macro → warning.
- Namespace collision (module @id == modulegroup @name) → incomplete.

Fixture design in `tests/fixtures/stations/TEST-{1,2}.00/`.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.rules import stations


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'stations' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'stations' / 'TEST-2.00'


class StationsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        cls.outputs = stations.run(ROOT1, ROOT2)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]

    # ---------- Case 1: Added ----------
    def test_station_added_with_dangling_group_ref_warning(self):
        """station_new is new; its @group points at a missing stationgroup →
        warning emitted + refs.station_group_unresolved=True."""
        matches = self._find(('station', 'station_new'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'station')
        self.assertIn('NEW', out.text)
        self.assertIn('station', out.extras['classifications'])
        self.assertIn('equipmentdock', out.extras['classifications'])
        refs = out.extras['refs']
        self.assertEqual(refs.get('group_ref'), 'nonexistent_sg')
        self.assertTrue(refs.get('station_group_unresolved'))
        # Warning emitted.
        warnings = [o for o in self.outputs
                    if o.extras.get('kind') == 'warning'
                    and 'nonexistent_sg' in o.text]
        self.assertTrue(warnings,
                        msg=f'no unresolved-group warning: '
                            f'{[o.text for o in self.outputs]}')

    def test_stationgroup_added(self):
        matches = self._find(('stationgroup', 'sg_new'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'stationgroup')
        self.assertIn('NEW', out.text)
        self.assertEqual(out.extras['refs']['plan_refs'], ['plan_new'])

    def test_module_added_shared_name(self):
        """shared_name module added — exists as a module @id and modulegroup
        @name simultaneously in TEST-2."""
        matches = self._find(('module', 'shared_name'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'module')
        # Classification prefix per spec: ["module", @class, ...tags, ...faction, ...race].
        self.assertEqual(out.extras['classifications'][0], 'module')
        self.assertIn('production', out.extras['classifications'])
        # Redundant 'module' token from <category @tags> is de-duped out.
        self.assertEqual(
            out.extras['classifications'].count('module'), 1,
            msg=f'duplicate module token: {out.extras["classifications"]}')

    def test_modulegroup_added_with_dangling_macro_ref(self):
        """shared_name modulegroup has select @macro=ghost_module, unresolved
        → ref_target_unresolved aggregate warning (one per owner, not per ref
        — see stations.md on modulegroup dangling refs being the norm)."""
        matches = self._find(('modulegroup', 'shared_name'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        warnings = [o for o in self.outputs
                    if o.extras.get('kind') == 'warning'
                    and o.extras.get('details', {}).get('ref_kind')
                    == 'module_macro_refs'
                    and o.extras.get('details', {}).get('owner_key')
                    == 'shared_name']
        self.assertEqual(
            len(warnings), 1,
            msg=f'expected 1 aggregate modulegroup warning: '
                f'{[o.text for o in self.outputs]}')
        self.assertIn('ghost_module',
                      warnings[0].extras['details']['unresolved_refs'])

    def test_constructionplan_added_with_typed_refs(self):
        """plan_new has one entry→module (mod_a), one entry→modulegroup (mg_b),
        one namespace-colliding entry (shared_name).

        Namespace collision → incomplete; typed refs still recorded."""
        matches = self._find(('constructionplan', 'plan_new'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'constructionplan')
        self.assertTrue(out.extras.get('incomplete'),
                        msg=f'plan_new not marked incomplete: {out.text}')
        refs = out.extras['refs']
        # mod_a + shared_name (collision retained) resolve to module.
        self.assertIn('mod_a', refs['entry_module_refs'])
        self.assertIn('shared_name', refs['entry_module_refs'])
        # mg_b + shared_name (collision retained) resolve to modulegroup.
        self.assertIn('mg_b', refs['entry_modulegroup_refs'])
        self.assertIn('shared_name', refs['entry_modulegroup_refs'])
        self.assertEqual(refs['entry_unresolved_refs'], [])
        self.assertIn('race=teladi', out.text)
        self.assertIn('total_entry_count=3', out.text)

    # ---------- Case 2: Removed ----------
    def test_station_removed(self):
        matches = self._find(('station', 'station_c'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')
        self.assertIn('REMOVED', matches[0].text)

    def test_stationgroup_removed(self):
        matches = self._find(('stationgroup', 'sg_to_remove'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')

    def test_module_removed(self):
        matches = self._find(('module', 'mod_to_remove'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')

    def test_modulegroup_removed(self):
        matches = self._find(('modulegroup', 'mg_to_remove'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')

    def test_constructionplan_removed(self):
        matches = self._find(('constructionplan', 'plan_to_remove'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')

    # ---------- Case 3: Modified ----------
    def test_station_modified_tags_and_faction(self):
        """station_a: tags changed from 'shipyard' to '[shipyard, mega]',
        faction from 'argon' to '[argon, antigone]'."""
        matches = self._find(('station', 'station_a'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('tags', out.text)
        self.assertIn('faction', out.text)

    def test_stationgroup_modified_plan_ref(self):
        """sg_a: select constructionplan=plan_a → plan_a_v2."""
        matches = self._find(('stationgroup', 'sg_a'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('plan_a', out.text)
        # plan_a removed, plan_a_v2 added.
        self.assertIn('removed', out.text)
        self.assertIn('added', out.text)

    def test_module_modified_production_chance_and_faction(self):
        """mod_a: category @faction change + production chance change."""
        matches = self._find(('module', 'mod_a'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('production[ware=energycells] chance 50→60', out.text)
        self.assertIn('category.faction', out.text)

    def test_modulegroup_modified_select_added(self):
        """mg_a: new select @macro=mod_b added."""
        matches = self._find(('modulegroup', 'mg_a'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('select[@macro=mod_b]', out.text)
        self.assertIn('total_entry_count', out.text)

    def test_constructionplan_modified_connection(self):
        """plan_a: entry[macro=mod_a,index=1] @connection slot_01→slot_02."""
        matches = self._find(('constructionplan', 'plan_a'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('connection slot_01→slot_02', out.text)

    # ---------- Case 4: Unchanged ----------
    def test_unchanged_station_emits_nothing(self):
        self.assertEqual(self._find(('station', 'station_b')), [])

    def test_unchanged_stationgroup_emits_nothing(self):
        self.assertEqual(self._find(('stationgroup', 'sg_b')), [])

    def test_unchanged_module_emits_nothing(self):
        self.assertEqual(self._find(('module', 'mod_b')), [])

    def test_unchanged_modulegroup_emits_nothing(self):
        self.assertEqual(self._find(('modulegroup', 'mg_b')), [])

    def test_unchanged_constructionplan_emits_nothing(self):
        self.assertEqual(self._find(('constructionplan', 'plan_b')), [])

    # ---------- Cross-entity ref graph ----------
    def test_plan_b_entry_resolves_to_modulegroup_bridge(self):
        """plan_b (unchanged so no diff row) still resolves its entry's
        @macro=mg_b to a modulegroup; no warning, no incomplete.

        Verified via plan_new which has mg_b as one entry — typed refs must
        still list mg_b under entry_modulegroup_refs.
        """
        matches = self._find(('constructionplan', 'plan_new'))
        self.assertEqual(len(matches), 1)
        refs = matches[0].extras['refs']
        self.assertIn('mg_b', refs['entry_modulegroup_refs'])

    def test_plan_new_entry_resolves_to_module_direct(self):
        """plan_new has mod_a as one entry — typed refs list it under
        entry_module_refs."""
        matches = self._find(('constructionplan', 'plan_new'))
        refs = matches[0].extras['refs']
        self.assertIn('mod_a', refs['entry_module_refs'])

    def test_namespace_collision_marks_plan_incomplete(self):
        """A `<entry @macro>` value matching BOTH module @id AND modulegroup
        @name surfaces as extras.incomplete=True."""
        matches = self._find(('constructionplan', 'plan_new'))
        self.assertTrue(matches[0].extras.get('incomplete'))
        # Sentinel emitted under subsource='constructionplan'.
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete'
                     and o.extras.get('subsource') == 'constructionplan']
        self.assertTrue(sentinels,
                        msg=f'no constructionplan incomplete sentinel: '
                            f'{[o.text for o in self.outputs]}')

    def test_station_group_dangling_emits_warning(self):
        """station_new.group_ref='nonexistent_sg' → warning with
        reason='ref_target_unresolved'."""
        warnings = [o for o in self.outputs
                    if o.extras.get('kind') == 'warning']
        self.assertTrue(any('nonexistent_sg' in w.text for w in warnings),
                        msg=f'no dangling-group warning: '
                            f'{[w.text for w in warnings]}')

    def test_modulegroup_dangling_macro_ref_warning(self):
        """modulegroup 'shared_name' select @macro='ghost_module' →
        ref_target_unresolved aggregate warning."""
        warnings = [o for o in self.outputs
                    if o.extras.get('kind') == 'warning'
                    and o.extras.get('details', {}).get('ref_kind')
                    == 'module_macro_refs']
        self.assertTrue(warnings,
                        msg=f'no module_macro_refs warning: '
                            f'{[o.text for o in self.outputs]}')
        # The unresolved list contains ghost_module.
        all_unresolved = set()
        for w in warnings:
            all_unresolved.update(w.extras['details']['unresolved_refs'])
        self.assertIn('ghost_module', all_unresolved)

    def test_contamination_scoped_per_subsource(self):
        """A namespace collision in constructionplan must not contaminate
        outputs in other sub-sources."""
        contaminated = {
            o.extras.get('subsource') for o in self.outputs
            if o.extras.get('incomplete')
            and o.extras.get('kind') != 'incomplete'
        }
        self.assertEqual(contaminated, {'constructionplan'},
                         msg=f'unexpected contamination spread: {contaminated}')

    def test_module_display_uses_id_when_identification_missing(self):
        """Without <identification> the module row falls back to @id."""
        matches = self._find(('module', 'mod_a'))
        self.assertEqual(len(matches), 1)
        # mod_a carries no <identification>, so the name is @id.
        self.assertIn('mod_a', matches[0].text)


class StationsRunSanityTest(unittest.TestCase):
    """Rule runs to completion and emits non-empty output list."""

    def test_run_smoke(self):
        cache.clear()
        outs = stations.run(ROOT1, ROOT2)
        self.assertIsInstance(outs, list)
        self.assertTrue(outs)


if __name__ == '__main__':
    unittest.main()
