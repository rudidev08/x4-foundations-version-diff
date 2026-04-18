"""Unit tests for the `drops` rule.

Covers the standard 9-case matrix across three sub-sources
(ammo / wares / droplist): added, removed, modified, DLC-sourced,
unchanged, classifications shape, multiset non-cascade on wares,
multiset non-cascade on droplist, and a sanity run.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.paths import reset_index
from src.rules import drops


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'drops' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'drops' / 'TEST-2.00'


class DropsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = drops.run(ROOT1, ROOT2)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]

    # ---------- Case 1: ammo added ----------
    def test_ammo_added(self):
        """basket_ammo_new is present only in TEST-2 (core)."""
        matches = self._find(('ammo', 'basket_ammo_new'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'ammo')
        self.assertEqual(out.extras['classifications'], ['ammo'])
        self.assertIn('NEW', out.text)
        self.assertIn('basket_ammo_new', out.text)

    # ---------- Case 2: ammo removed ----------
    def test_ammo_removed(self):
        """basket_ammo_removed is present only in TEST-1."""
        matches = self._find(('ammo', 'basket_ammo_removed'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'ammo')
        self.assertIn('REMOVED', out.text)

    # ---------- Case 3: ammo modified (keyed-by-@macro select diff) ----------
    def test_ammo_modified(self):
        """basket_ammo_01: weight change, max change, one removed, one added."""
        matches = self._find(('ammo', 'basket_ammo_01'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'ammo')
        # Weight bump on cluster.
        self.assertIn('select[macro=missile_cluster_light_mk1_macro] weight 7→9',
                      out.text)
        # Max bump on dumbfire mk2.
        self.assertIn('select[macro=missile_dumbfire_light_mk2_macro] max 4→5',
                      out.text)
        # EMP removed.
        self.assertIn('select[macro=missile_emp_mk1_macro] removed', out.text)
        # Heatseeker added.
        self.assertIn('select[macro=missile_heatseeker_light_mk1_macro] added',
                      out.text)

    # ---------- Case 4: wares added (DLC-sourced) ----------
    def test_wares_added_from_dlc(self):
        """basket_wares_boron_01 is added via boron DLC in TEST-2."""
        matches = self._find(('wares', 'basket_wares_boron_01'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'wares')
        self.assertEqual(out.extras['classifications'], ['wares'])
        self.assertIn('NEW', out.text)
        # DLC source short name (boron) must be in the sources label.
        self.assertIn('boron', out.text)

    # ---------- Case 5: droplist removed ----------
    def test_droplist_removed(self):
        """drops_tutorial is present only in TEST-1."""
        matches = self._find(('droplist', 'drops_tutorial'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'droplist')
        self.assertIn('REMOVED', out.text)

    # ---------- Case 6: droplist added ----------
    def test_droplist_added(self):
        """drops_new_mission is added in TEST-2."""
        matches = self._find(('droplist', 'drops_new_mission'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'droplist')
        self.assertEqual(out.extras['classifications'], ['droplist'])
        self.assertIn('NEW', out.text)

    # ---------- Case 7: wares multiset non-cascade ----------
    def test_wares_multiset_non_cascade(self):
        """basket_wares_common_01 has shared-sig selects.

        TEST-1:
          (weight=7, [(algaescrubber, 1)]) x2
          (weight=10, [(carbonfilter, 2)])
          (weight=5,  [(microgimble, 3)])
        TEST-2:
          (weight=7, [(algaescrubber, 1)])
          (weight=7, [(algaescrubber, 5)])
          (weight=10, [(carbonfilter, 2)])
          (weight=8,  [(newware, 2)])

        Multiset diff should produce:
        - 1 pair of (weight=7, algaescrubber, 1) survives unchanged (no
          output for the pair).
        - 1 OLD-only (weight=7, algaescrubber, 1) → "select removed".
          Wait — actually 2 on old side, 1 on new side → one unpaired on
          old side → "removed". ✓
        - 1 NEW-only (weight=7, algaescrubber, 5) → "select added".
        - (weight=10, carbonfilter, 2) pair → no output.
        - 1 OLD-only (weight=5, microgimble, 3) → "select removed".
        - 1 NEW-only (weight=8, newware, 2) → "select added".
        Net: 2 added, 2 removed lines — NO modified.
        """
        matches = self._find(('wares', 'basket_wares_common_01'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'wares')
        # The text joins change parts with ", " — count added/removed.
        added_count = out.text.count('select added')
        removed_count = out.text.count('select removed')
        self.assertEqual(
            added_count, 2,
            msg=f'expected 2 added, got {added_count}: {out.text!r}',
        )
        self.assertEqual(
            removed_count, 2,
            msg=f'expected 2 removed, got {removed_count}: {out.text!r}',
        )
        # No '→' modified-style transitions on select entries.
        # (Ammo's attr changes use →, but wares multiset must not.)
        self.assertNotIn('select modified', out.text)
        # Added row carries weight=7 algaescrubber amount=5 (the shifted
        # duplicate).
        self.assertIn('weight=7 wares=[inv_algaescrubber:5]', out.text)
        # Added row carries newware.
        self.assertIn('weight=8 wares=[inv_newware:2]', out.text)
        # Removed row carries microgimble.
        self.assertIn('weight=5 wares=[inv_microgimble:3]', out.text)
        # One of the weight=7 algaescrubber amount=1 duplicates stays
        # (paired), the other is removed.
        self.assertIn('weight=7 wares=[inv_algaescrubber:1]', out.text)

    # ---------- Case 8: droplist multiset non-cascade ----------
    def test_droplist_multiset_non_cascade(self):
        """drops_crystal_s_01 has shared-attr-sig drops.

        TEST-1:
          (chance=20, …) x2 with <ware unstablecrystal amount=1>
          (no-chance, min=5, max=10) with <ware crystal_01 amount=2 chance=100>
        TEST-2:
          (chance=20, …) with <ware unstablecrystal amount=1>
          (chance=30, …) with <ware unstablecrystal amount=1>
          (no-chance, min=5, max=10) with <ware crystal_01 amount=3 chance=100>

        Expected per spec signature (drop attrs + direct ware payload):
        - Sig A = (chance=20, macro=..., min=1, max=1) + (unstablecrystal,
          1, None). Old: 2, new: 1 → 1 pair + 1 OLD removed.
        - Sig B = (chance=30, macro=..., min=1, max=1) + (unstablecrystal,
          1, None). Old: 0, new: 1 → 1 NEW added.
        - Sig C = (macro=..., min=5, max=10) + (crystal_01, 2, 100). Old:
          1, new: 0 → 1 OLD removed.
        - Sig D = (macro=..., min=5, max=10) + (crystal_01, 3, 100). Old:
          0, new: 1 → 1 NEW added.
        Net: 2 added, 2 removed; NO modified.
        """
        matches = self._find(('droplist', 'drops_crystal_s_01'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'droplist')
        added_count = out.text.count('drop added')
        removed_count = out.text.count('drop removed')
        self.assertEqual(
            added_count, 2,
            msg=f'expected 2 drop added, got {added_count}: {out.text!r}',
        )
        self.assertEqual(
            removed_count, 2,
            msg=f'expected 2 drop removed, got {removed_count}: {out.text!r}',
        )
        self.assertNotIn('drop modified', out.text)
        # Added row with chance=30.
        self.assertIn('chance=30', out.text)
        # Added crystal_01:3
        self.assertIn('inv_crystal_01', out.text)

    # ---------- Case 9: unchanged entity emits nothing ----------
    def test_wares_unchanged_emits_nothing(self):
        """basket_wares_unchanged is identical across TEST-1 and TEST-2."""
        matches = self._find(('wares', 'basket_wares_unchanged'))
        self.assertEqual(matches, [],
                         msg=f'unexpected row(s): {[m.text for m in matches]}')

    # ---------- Classifications: each kind ----------
    def test_classifications_per_subsource(self):
        """Each sub-source emits its literal token in classifications."""
        for entity_key, expected in [
            (('ammo', 'basket_ammo_new'), 'ammo'),
            (('wares', 'basket_wares_boron_01'), 'wares'),
            (('droplist', 'drops_new_mission'), 'droplist'),
        ]:
            matches = self._find(entity_key)
            self.assertEqual(len(matches), 1,
                             msg=f'{entity_key}: {[o.text for o in self.outputs]}')
            self.assertEqual(matches[0].extras['classifications'], [expected])

    # ---------- Sanity: rule produces outputs ----------
    def test_run_does_not_crash(self):
        self.assertTrue(self.outputs)


class DropsHelpersTest(unittest.TestCase):
    """Direct tests for internal helpers."""

    def test_classifications_helper(self):
        self.assertEqual(drops._classifications('ammo'), ['ammo'])
        self.assertEqual(drops._classifications('wares'), ['wares'])
        self.assertEqual(drops._classifications('droplist'), ['droplist'])

    def test_wares_select_signature_includes_weight_and_ware_tuples(self):
        import xml.etree.ElementTree as ET
        sel = ET.fromstring(
            '<select weight="7">'
            '<ware ware="b" amount="2"/>'
            '<ware ware="a" amount="1"/>'
            '</select>'
        )
        sig = drops._wares_select_signature(sel)
        # Weight first.
        self.assertEqual(sig[0], '7')
        # Ware tuples sorted — (a, 1) before (b, 2).
        self.assertEqual(sig[1], (('a', '1'), ('b', '2')))

    def test_drop_signature_includes_attrs_and_direct_wares(self):
        import xml.etree.ElementTree as ET
        d = ET.fromstring(
            '<drop macro="m" chance="20">'
            '<ware ware="w1" amount="1" chance="50"/>'
            '</drop>'
        )
        sig = drops._drop_signature(d)
        self.assertEqual(sig[0], (('chance', '20'), ('macro', 'm')))
        self.assertEqual(sig[1], (('w1', '1', '50'),))

    def test_drop_signature_distinguishes_attrs_even_with_same_payload(self):
        import xml.etree.ElementTree as ET
        d1 = ET.fromstring('<drop macro="m" chance="20"/>')
        d2 = ET.fromstring('<drop macro="m" chance="30"/>')
        # Same empty payload but distinct attrs.
        self.assertNotEqual(drops._drop_signature(d1), drops._drop_signature(d2))

    def test_multiset_select_diff_pairs_shared_sigs(self):
        """min(old_count, new_count) pairs survive unchanged."""
        import xml.etree.ElementTree as ET
        a = ET.fromstring('<select weight="5"><ware ware="x" amount="1"/></select>')
        b = ET.fromstring('<select weight="5"><ware ware="x" amount="1"/></select>')
        # Both sides identical; no output.
        sig_a = drops._wares_select_signature(a)
        sig_b = drops._wares_select_signature(b)
        out = drops._multiset_select_diff(
            [a], [sig_a], [b], [sig_b],
            label='select', fmt_fn=drops._fmt_wares_select,
        )
        self.assertEqual(out, [])


if __name__ == '__main__':
    unittest.main()
