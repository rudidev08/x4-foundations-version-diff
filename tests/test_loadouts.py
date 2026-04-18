"""Unit tests for the `loadouts` rule.

Covers the 9-case matrix across both sub-sources (`loadout` / `rule`):
added, removed, modified, multi-field modified, unchanged, classifications,
composite-key stability, refs payload, and the multiset non-cascade path.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.paths import reset_index
from src.rules import loadouts


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'loadouts' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'loadouts' / 'TEST-2.00'


class LoadoutsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = loadouts.run(ROOT1, ROOT2)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]

    def _find_any(self, predicate):
        return [o for o in self.outputs if predicate(o)]

    # ---------- Case 1: loadout added ----------
    def test_loadout_added_resolves_name_via_macro(self):
        """New loadout `arg_trader_default` resolves via ship_arg_m_trader_01."""
        matches = self._find(('loadout', 'arg_trader_default'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'loadout')
        self.assertIn('NEW', out.text)
        self.assertIn('Argon M Trader', out.text)
        self.assertEqual(out.extras['classifications'], ['loadout'])
        self.assertEqual(
            out.extras['refs'], {'ship_macro': 'ship_arg_m_trader_01_macro'},
        )

    # ---------- Case 2: loadout removed ----------
    def test_loadout_removed(self):
        matches = self._find(('loadout', 'arg_legacy_removed'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'loadout')
        self.assertIn('REMOVED', out.text)
        self.assertEqual(
            out.extras['refs'], {'ship_macro': 'ship_arg_m_fighter_01_macro'},
        )

    # ---------- Case 3: loadout modified (multi-field) ----------
    def test_loadout_modified_multi_field(self):
        """arg_fighter_default: engine bumped, shield bumped, software added/removed."""
        matches = self._find(('loadout', 'arg_fighter_default'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'loadout')
        self.assertIn('engine', out.text)
        self.assertIn('shield', out.text)
        self.assertIn('software', out.text)
        self.assertIn('software_dockmk1', out.text)  # removed
        self.assertIn('software_dockmk2', out.text)  # added
        self.assertIn('software_targetmk1', out.text)  # added

    # ---------- Case 4: loadout unchanged emits nothing ----------
    def test_loadout_unchanged_emits_nothing(self):
        matches = self._find(('loadout', 'arg_scout_default'))
        self.assertEqual(matches, [])

    # ---------- Case 5: rule removed (category/mk composite key) ----------
    def test_rule_removed(self):
        """unit/default/defence/mk1 bucket only exists in TEST-1.00."""
        key = ('rule', ('unit', 'default', 'defence', '1', ('ship_m',),
                        (), (), ()))
        matches = self._find(key)
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertIn('REMOVED', out.text)
        self.assertIn('weight=30', out.text)

    # ---------- Case 6: rule added (new composite key on new side) ----------
    def test_rule_added_unit_xenon_transport(self):
        key = ('rule', ('unit', 'xenon', 'transport', '1', ('buildmodule',),
                        (), (), ()))
        matches = self._find(key)
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertIn('NEW', out.text)
        self.assertIn('weight=30', out.text)
        self.assertIn('unit', out.extras['classifications'])
        self.assertIn('xenon', out.extras['classifications'])
        self.assertIn('buildmodule', out.extras['classifications'])

    # ---------- Case 7: rule modified (single-rule paired diff) ----------
    def test_rule_modified_weight_bump(self):
        """unit/default/transport/mk1 weight 20→25: unique applicability → paired."""
        key = ('rule', ('unit', 'default', 'transport', '1',
                        ('ship_l', 'ship_xl'), ('mine', 'trade'), (), ()))
        matches = self._find(key)
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('weight 20→25', out.text)
        # Applicability is stable; refs carries the full composite key.
        refs = out.extras['refs']['applicability']
        self.assertEqual(refs['container'], 'unit')
        self.assertEqual(refs['ruleset_type'], 'default')
        self.assertEqual(refs['category'], 'transport')
        self.assertEqual(refs['mk'], '1')
        self.assertEqual(refs['classes'], ['ship_l', 'ship_xl'])
        self.assertEqual(refs['purposes'], ['mine', 'trade'])

    # ---------- Case 8: rule unchanged emits nothing ----------
    def test_rule_unchanged_emits_nothing(self):
        """deployable/default/satellite/mk1 is unchanged — no output."""
        key = ('rule', ('deployable', 'default', 'satellite', '1',
                        ('ship_s',), ('fight',), (), ()))
        matches = self._find(key)
        self.assertEqual(matches, [], msg=[o.text for o in self.outputs])

    def test_rule_unchanged_player_repair(self):
        """unit/player/repair/mk1 is unchanged across versions."""
        key = ('rule', ('unit', 'player', 'repair', '1',
                        ('ship_l', 'ship_xl'), (), (), ()))
        matches = self._find(key)
        self.assertEqual(matches, [])

    # ---------- Case 9: composite key stability (entity_key repr) ----------
    def test_composite_key_is_tuple_not_frozenset(self):
        """entity_key must use tuple(sorted(...)) so repr is stable across runs."""
        rule_keys = [o.extras['entity_key'] for o in self.outputs
                     if o.extras.get('subsource') == 'rule']
        for key in rule_keys:
            self.assertEqual(key[0], 'rule')
            composite = key[1]
            # Every multi-value slot should be a plain tuple, not frozenset.
            for idx in (4, 5, 6, 7):
                self.assertIsInstance(
                    composite[idx], tuple,
                    msg=f'composite slot {idx} should be tuple: {composite!r}',
                )

    # ---------- Multiset non-cascade ----------
    def test_multiset_applicability_non_cascade(self):
        """Two rules share composite key (police/mk1/buildmodule/watchdoguser).

        Old: [weight=5, weight=10]
        New: [weight=5, weight=20]

        Signature-multiset diff:
        - Common sig {weight=5}: 1 pair, no output.
        - Old-only sig {weight=10}: 1 REMOVED.
        - New-only sig {weight=20}: 1 ADDED.

        No 'modified' output — multiset suppresses the cascade that would
        otherwise report "weight 10→20" for the bumped rule and "removed"
        for the weight=10 rule.
        """
        key = ('rule', ('unit', 'default', 'police', '1', ('buildmodule',),
                        (), ('watchdoguser',), ()))
        matches = self._find(key)
        kinds = [o.extras['kind'] for o in matches]
        self.assertEqual(
            sorted(kinds), ['added', 'removed'],
            msg=f'expected 1 added + 1 removed, got {kinds}: '
                f'{[m.text for m in matches]}',
        )
        # Confirm the added row carries weight=20 (the new sig).
        added_row = [m for m in matches if m.extras['kind'] == 'added'][0]
        self.assertIn('weight=20', added_row.text)
        self.assertTrue(added_row.extras.get('multiset'))
        # Confirm the removed row carries weight=10 (the old-only sig).
        removed_row = [m for m in matches if m.extras['kind'] == 'removed'][0]
        self.assertIn('weight=10', removed_row.text)
        self.assertTrue(removed_row.extras.get('multiset'))
        # Confirm NO 'modified' row for this key.
        modifieds = [m for m in matches if m.extras['kind'] == 'modified']
        self.assertEqual(
            modifieds, [],
            msg='multiset path must not emit modified (cascade prevention)',
        )


class LoadoutsHelpersTest(unittest.TestCase):
    """Direct unit tests for the internal helpers."""

    def test_rule_signature_excludes_applicability_attrs(self):
        import xml.etree.ElementTree as ET
        e = ET.fromstring(
            '<rule category="repair" mk="1" weight="10" important="true" '
            'classes="ship_l"/>'
        )
        sig = loadouts._rule_signature(e)
        # Applicability attrs (category, mk, classes) must NOT appear.
        attr_names = [k for k, _ in sig]
        self.assertNotIn('category', attr_names)
        self.assertNotIn('mk', attr_names)
        self.assertNotIn('classes', attr_names)
        # Weight and important do appear.
        self.assertIn('weight', attr_names)
        self.assertIn('important', attr_names)

    def test_rule_composite_key_none_when_missing_parents(self):
        """A bare <rule> with no ancestor unit/deployable + ruleset returns None."""
        import xml.etree.ElementTree as ET
        e = ET.fromstring('<rule category="x" mk="1"/>')
        key = loadouts._rule_composite_key(e, {})
        self.assertIsNone(key)

    def test_slot_bag_uses_tuple_identity(self):
        import xml.etree.ElementTree as ET
        loadout = ET.fromstring(
            '<loadout>'
            '<macros>'
            '<engine macro="eng_mk1_macro" path="../c1"/>'
            '<engine macro="eng_mk1_macro" path="../c2"/>'
            '</macros>'
            '</loadout>'
        )
        bag = loadouts._slot_bag(loadout, 'engine')
        # Two engines with same macro, different paths → 2 distinct bag members.
        self.assertEqual(len(bag), 2)


if __name__ == '__main__':
    unittest.main()
