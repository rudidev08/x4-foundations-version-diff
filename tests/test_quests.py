"""Unit tests for the `quests` rule.

Covers the file-level 9-case matrix (add, remove, modify, DLC-sourced,
DLC-same-filename = two entities, truncation, multibyte-near-boundary
stability, classifications, unchanged-emits-nothing) plus direct helper
tests for name extraction and prefix classification.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.file_level import render_modified
from src.lib.paths import reset_index
from src.rules import quests


HERE = Path(__file__).resolve().parent
FIX = HERE / 'fixtures' / 'quests'
ROOT1 = FIX / 'TEST-1.00'
ROOT2 = FIX / 'TEST-2.00'
LARGE_OLD = FIX / '_large' / 'old'
LARGE_NEW = FIX / '_large' / 'new'


class QuestsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = quests.run(ROOT1, ROOT2)

    def _by_key(self, key):
        return [o for o in self.outputs if o.extras.get('entity_key') == key]

    # ---------- Case 1: added ----------
    def test_added_file(self):
        matches = self._by_key('md/gm_new_mission.xml')
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['sources'], ['core'])
        self.assertEqual(out.extras['source_files'], ['md/gm_new_mission.xml'])
        self.assertEqual(out.extras['classifications'], ['generic_mission'])
        self.assertIn('ADDED', out.text)
        # Display name pulled from <mdscript @name>.
        self.assertIn('GM_NewMission', out.text)
        # `+N lines` summary.
        self.assertRegex(out.text, r'ADDED \(\+\d+ lines\)')

    # ---------- Case 2: removed ----------
    def test_removed_file(self):
        matches = self._by_key('md/story_deprecated.xml')
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['sources'], ['core'])
        self.assertEqual(out.extras['classifications'], ['story'])
        self.assertIn('REMOVED', out.text)
        self.assertIn('Story_Deprecated', out.text)
        self.assertRegex(out.text, r'REMOVED \(-\d+ lines\)')

    # ---------- Case 3: modified (small diff, exact counts) ----------
    def test_modified_small_diff(self):
        matches = self._by_key('md/trade_basic.xml')
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['classifications'], ['trade'])
        # TEST-1 has one `<set_value>` line; TEST-2 has two with different
        # values → +2/-1.
        self.assertEqual(out.extras['added_lines'], 2)
        self.assertEqual(out.extras['removed_lines'], 1)
        self.assertIn('+2/-1', out.text)
        self.assertIn('Trade_Basic', out.text)
        # Raw unified diff carries the new lines.
        self.assertIn('<set_value name="$Price" exact="150"/>',
                      out.extras['diff'])
        self.assertIn('<set_value name="$Tax" exact="5"/>',
                      out.extras['diff'])
        self.assertFalse(out.extras['diff_truncated'])

    # ---------- Case 4: lifecycle (n/a at file level — skipped) ----------
    # See quests.md §"What the rule does NOT cover" — files only have
    # add/remove/modify semantics.

    # ---------- Case 5: DLC-sourced ----------
    def test_dlc_only_file(self):
        key = 'extensions/ego_dlc_timelines/md/scenario_timelines_intro.xml'
        matches = self._by_key(key)
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['sources'], ['timelines'])
        self.assertEqual(out.extras['classifications'], ['scenario'])
        self.assertIn('ADDED', out.text)

    # ---------- DLC-same-filename: two distinct rows ----------
    def test_dlc_same_filename_yields_two_rows(self):
        """`md/factionlogic.xml` exists in core AND in ego_dlc_boron.

        Assert each is a distinct entity with its own entity_key, source,
        and diff. No merging.
        """
        core_matches = self._by_key('md/factionlogic.xml')
        dlc_matches = self._by_key(
            'extensions/ego_dlc_boron/md/factionlogic.xml'
        )
        self.assertEqual(len(core_matches), 1,
                         msg=[o.text for o in self.outputs])
        self.assertEqual(len(dlc_matches), 1,
                         msg=[o.text for o in self.outputs])
        self.assertEqual(core_matches[0].extras['sources'], ['core'])
        self.assertEqual(dlc_matches[0].extras['sources'], ['boron'])
        # Entity keys must differ — no merging.
        self.assertNotEqual(
            core_matches[0].extras['entity_key'],
            dlc_matches[0].extras['entity_key'],
        )
        # Both classified as factionlogic.
        self.assertEqual(core_matches[0].extras['classifications'],
                         ['factionlogic'])
        self.assertEqual(dlc_matches[0].extras['classifications'],
                         ['factionlogic'])
        # Core diff must not contain DLC-specific text (BoronDiplomacy).
        self.assertNotIn('BoronDiplomacy', core_matches[0].extras['diff'])
        self.assertIn('BoronDiplomacy', dlc_matches[0].extras['diff'])

    # ---------- Case 6: moved file = remove + add ----------
    # Not a separate fixture; implicit in the design. A rename shows up as
    # two rows (one REMOVED, one ADDED) just as if the content were deleted
    # and reintroduced. Covered by case 1 + case 2 combined behavior.

    # ---------- Case 7: truncation ----------
    def test_truncation_on_large_file(self):
        cache.clear()
        reset_index()
        outs = quests.run(LARGE_OLD, LARGE_NEW)
        matches = [o for o in outs
                   if o.extras.get('entity_key') == 'md/gm_huge.xml']
        self.assertEqual(len(matches), 1, msg=[o.text for o in outs])
        out = matches[0]
        self.assertTrue(out.extras['diff_truncated'],
                        msg=f'expected truncation; got {out.text}')
        self.assertIn('truncated', out.extras['diff'])

    # ---------- Case 8a: render_modified is stable across reruns ----------
    def test_render_modified_stable_across_reruns(self):
        """Same inputs → identical output, bytes-for-bytes. Load-bearing
        for Tier B snapshot hashing.
        """
        old = (LARGE_OLD / 'md' / 'gm_huge.xml').read_bytes()
        new = (LARGE_NEW / 'md' / 'gm_huge.xml').read_bytes()
        t1, e1 = render_modified('md/gm_huge.xml', old, new,
                                 tag='quests', name='GM_Huge')
        t2, e2 = render_modified('md/gm_huge.xml', old, new,
                                 tag='quests', name='GM_Huge')
        self.assertEqual(t1, t2)
        self.assertEqual(e1['diff'], e2['diff'])
        self.assertEqual(e1['added_lines'], e2['added_lines'])
        self.assertEqual(e1['removed_lines'], e2['removed_lines'])
        self.assertEqual(e1['diff_truncated'], e2['diff_truncated'])

    # ---------- Case 8b: multibyte-UTF-8 near truncation boundary ----------
    def test_multibyte_preserved_across_truncation(self):
        """The fixture seeds multibyte chars every ~250 lines so some
        land in both the head and tail windows. The truncated diff must
        still decode cleanly as UTF-8.
        """
        cache.clear()
        reset_index()
        outs = quests.run(LARGE_OLD, LARGE_NEW)
        out = [o for o in outs
               if o.extras.get('entity_key') == 'md/gm_huge.xml'][0]
        diff_text = out.extras['diff']
        # Round-trip: encoding then decoding must be lossless.
        self.assertEqual(diff_text.encode('utf-8').decode('utf-8'), diff_text)
        # At least one of the seeded multibyte tokens survived.
        self.assertTrue(
            any(tok in diff_text for tok in ('日本語', '漢字', 'δ')),
            msg='expected a multibyte token to survive truncation',
        )

    # ---------- Case 9: unchanged files emit nothing ----------
    def test_unchanged_file_emits_nothing(self):
        """Files present in both trees with identical bytes do not appear
        in `diff_files` output; the rule emits nothing for them.

        The fixture's md/ trees include no unchanged files by design
        (every file is added / removed / modified). Add an identical
        pair inline and assert it stays out.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            root1 = Path(t) / 'v1'
            root2 = Path(t) / 'v2'
            (root1 / 'md').mkdir(parents=True)
            (root2 / 'md').mkdir(parents=True)
            payload = (b'<?xml version="1.0"?>\n'
                       b'<mdscript name="GS_Unchanged"/>\n')
            (root1 / 'md' / 'gs_unchanged.xml').write_bytes(payload)
            (root2 / 'md' / 'gs_unchanged.xml').write_bytes(payload)
            cache.clear()
            reset_index()
            outs = quests.run(root1, root2)
        self.assertEqual(outs, [])


class QuestsHelpersTest(unittest.TestCase):
    """Direct tests for internal helpers (classifications, display name)."""

    def test_classifications_prefix_map(self):
        self.assertEqual(quests._classifications('md/gm_foo.xml'),
                         ['generic_mission'])
        self.assertEqual(quests._classifications('md/story_intro.xml'),
                         ['story'])
        self.assertEqual(quests._classifications('md/factionlogic_x.xml'),
                         ['factionlogic'])
        self.assertEqual(quests._classifications('md/scenario_y.xml'),
                         ['scenario'])
        self.assertEqual(quests._classifications('md/gs_tutorial.xml'),
                         ['gamestart'])
        self.assertEqual(quests._classifications('md/trade_basic.xml'),
                         ['trade'])

    def test_classifications_bare_stem_literal(self):
        self.assertEqual(quests._classifications('md/notifications.xml'),
                         ['notification'])

    def test_classifications_bare_stem_via_prefix(self):
        """`factionlogic.xml` (no underscore) has prefix=`factionlogic`."""
        self.assertEqual(quests._classifications('md/factionlogic.xml'),
                         ['factionlogic'])

    def test_classifications_unknown_prefix_empty(self):
        self.assertEqual(quests._classifications('md/zzz_other.xml'), [])
        self.assertEqual(quests._classifications('md/novel.xml'), [])

    def test_classifications_works_on_dlc_paths(self):
        """Path prefix doesn't affect classification — only the basename does."""
        self.assertEqual(
            quests._classifications(
                'extensions/ego_dlc_boron/md/gm_boron_rescue.xml'
            ),
            ['generic_mission'],
        )

    def test_display_name_from_mdscript_attr(self):
        xml = (b'<?xml version="1.0"?>\n'
               b'<mdscript name="MyScript"><cues/></mdscript>\n')
        self.assertEqual(quests._display_name('md/any.xml', xml), 'MyScript')

    def test_display_name_fallback_to_stem_on_parse_error(self):
        garbage = b'\xff\xfe not xml at all'
        self.assertEqual(
            quests._display_name('md/broken.xml', garbage),
            'broken',
        )

    def test_display_name_fallback_on_non_mdscript_root(self):
        xml = b'<?xml version="1.0"?><other name="X"/>'
        self.assertEqual(
            quests._display_name('md/weird.xml', xml),
            'weird',
        )

    def test_display_name_fallback_when_mdscript_lacks_name(self):
        xml = b'<?xml version="1.0"?><mdscript><cues/></mdscript>'
        self.assertEqual(
            quests._display_name('md/nameless.xml', xml),
            'nameless',
        )


if __name__ == '__main__':
    unittest.main()
