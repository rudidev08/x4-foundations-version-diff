"""Unit tests for the `unlocks` rule.

Covers the standard 9-case matrix across the three sub-sources
(discount / chapter / info): added, removed, modified, DLC-sourced,
incomplete (condition_type_not_unique + action_type_not_unique),
unchanged, plus provenance handoff.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.paths import reset_index
from src.rules import unlocks


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'unlocks' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'unlocks' / 'TEST-2.00'


class UnlocksRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = unlocks.run(ROOT1, ROOT2)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]

    # ---------- Case 1: Added (info sub-source) ----------
    def test_added_info(self):
        """info_added appears only in TEST-2 — a new <info @type=...>."""
        matches = self._find(('info', 'info_added'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'info')
        self.assertIn('NEW', out.text)
        self.assertIn('percent=80', out.text)
        self.assertIn('info_added', out.text)
        self.assertIn('info', out.extras['classifications'])

    # ---------- Case 2: Removed (discount sub-source) ----------
    def test_removed_discount(self):
        """disc_removed_buggy is in TEST-1 only."""
        matches = self._find(('discount', 'disc_removed_buggy'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'discount')
        self.assertIn('REMOVED', out.text)
        self.assertIn('Removed Buggy Discount', out.text)
        self.assertIn('discount', out.extras['classifications'])

    # ---------- Case 3: Modified discount — attrs + conditions + actions ----------
    def test_modified_discount(self):
        """disc_modified has changed @weight, @distance, wares @sells, and
        <amount> @max in actions block."""
        matches = self._find(('discount', 'disc_modified'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'discount')
        self.assertIn('Modified Discount', out.text)
        # Conditions block @weight change surfaces on the block attrs.
        self.assertIn('conditions weight 20→30', out.text)
        # Child <wares> @sells change surfaces as keyed-by-tag.
        self.assertIn('conditions.wares sells energycells→energycells graphene',
                      out.text)
        # <distance> exact attr change.
        self.assertIn('conditions.distance exact 600m→500m', out.text)
        # Actions <amount> @max change.
        self.assertIn('actions.amount max 6→8', out.text)

    # ---------- Case 4: DLC-sourced Added (chapter via ventures DLC) ----------
    def test_chapter_added_via_dlc(self):
        """chapter_added_dlc is added to TEST-2 via the ventures DLC patch."""
        matches = self._find(('chapter', 'chapter_added_dlc'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'chapter')
        self.assertIn('NEW', out.text)
        self.assertIn('Added DLC Chapter', out.text)
        # Provenance: the ventures DLC contributed this entity.
        self.assertIn('ventures', out.extras['sources'])

    # ---------- Case 5: Modified chapter ----------
    def test_modified_chapter(self):
        """chapter_modified has @group + @teamware + @highlight changes."""
        matches = self._find(('chapter', 'chapter_modified'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'chapter')
        self.assertIn('Modified Chapter', out.text)
        self.assertIn('group 1→2', out.text)
        self.assertIn('teamware old_teamware→new_teamware', out.text)
        self.assertIn('highlight None→true', out.text)

    # ---------- Case 6: Modified info ----------
    def test_modified_info(self):
        """info_percent_changed has an @percent change."""
        matches = self._find(('info', 'info_percent_changed'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'info')
        self.assertIn('percent 30→50', out.text)

    # ---------- Case 7: condition_type_not_unique incomplete ----------
    def test_condition_type_not_unique_incomplete(self):
        """disc_removed_buggy has two <scannerlevel> children in its
        <conditions> block — the keyed-by-tag contract is violated and the
        uniqueness assertion fires."""
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete'
                     and o.extras.get('subsource') == 'discount']
        self.assertTrue(sentinels,
                        msg=f'no discount-sub-source incomplete sentinel '
                            f'in {[o.text for o in self.outputs]}')
        sentinel = sentinels[0]
        reasons = [f[1].get('reason') for f in sentinel.extras['failures']]
        self.assertIn('condition_type_not_unique', reasons,
                      msg=f'reasons={reasons}')
        aks = [f[1].get('affected_keys') for f in sentinel.extras['failures']
               if f[1].get('reason') == 'condition_type_not_unique']
        self.assertTrue(
            any(('discount', 'disc_removed_buggy') in (ak or [])
                for ak in aks),
            msg=f'affected_keys={aks}',
        )

    # ---------- Case 8: action_type_not_unique incomplete ----------
    def test_action_type_not_unique_incomplete(self):
        """disc_removed_buggy has two <duration> children in its <actions>
        block — the keyed-by-tag contract is violated for actions too."""
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete'
                     and o.extras.get('subsource') == 'discount']
        self.assertTrue(sentinels)
        sentinel = sentinels[0]
        reasons = [f[1].get('reason') for f in sentinel.extras['failures']]
        self.assertIn('action_type_not_unique', reasons,
                      msg=f'reasons={reasons}')
        aks = [f[1].get('affected_keys') for f in sentinel.extras['failures']
               if f[1].get('reason') == 'action_type_not_unique']
        self.assertTrue(
            any(('discount', 'disc_removed_buggy') in (ak or [])
                for ak in aks),
            msg=f'affected_keys={aks}',
        )
        # The disc_removed_buggy row must be flagged incomplete via
        # forward_incomplete's affected_keys propagation.
        removed = self._find(('discount', 'disc_removed_buggy'))
        self.assertEqual(len(removed), 1)
        self.assertTrue(removed[0].extras.get('incomplete'),
                        msg=f'buggy row not incomplete: {removed[0].text}')

    # ---------- Case 9: Unchanged entities emit no row ----------
    def test_unchanged_info_emits_no_row(self):
        """Only the explicitly-changed entities should produce rows. The
        fixtures contain no fully-unchanged entities; this test guards
        against accidental emission of identical-content rows by asserting
        the emitted rows match the expected content entity-keys exactly."""
        content_keys = sorted(
            (o.extras.get('entity_key'),
             o.extras.get('kind'),
             o.extras.get('subsource'))
            for o in self.outputs
            if o.extras.get('kind') in ('added', 'removed', 'modified')
        )
        expected = sorted([
            (('discount', 'disc_removed_buggy'), 'removed', 'discount'),
            (('discount', 'disc_modified'), 'modified', 'discount'),
            (('chapter', 'chapter_added_dlc'), 'added', 'chapter'),
            (('chapter', 'chapter_modified'), 'modified', 'chapter'),
            (('info', 'info_added'), 'added', 'info'),
            (('info', 'info_percent_changed'), 'modified', 'info'),
        ])
        self.assertEqual(content_keys, expected)

    # ---------- Sanity: rule produces outputs ----------
    def test_run_does_not_crash(self):
        self.assertTrue(self.outputs)


class UnlocksHelpersTest(unittest.TestCase):
    """Direct tests for helpers: the duplicate-type scanner and formatting."""

    def test_generic_filter_is_empty(self):
        """All subsource tokens are information-bearing; filter is empty."""
        self.assertEqual(unlocks._GENERIC_FILTER, frozenset())

    def test_chapter_attrs_is_the_documented_set(self):
        self.assertEqual(unlocks._CHAPTER_ATTRS,
                         ('group', 'highlight', 'teamware'))

    def test_format_with_classifications(self):
        text = unlocks._format('My Discount', ['discount'], '[core]',
                               ['NEW'])
        self.assertEqual(text, '[unlocks] My Discount (discount) [core]: NEW')


if __name__ == '__main__':
    unittest.main()
