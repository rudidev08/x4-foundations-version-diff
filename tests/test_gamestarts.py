import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.rules import gamestarts


HERE = Path(__file__).resolve().parent


class GamestartsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root1 = HERE / 'fixtures' / 'gamestarts' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'gamestarts' / 'TEST-2.00'

    def setUp(self):
        cache.clear()
        self.outputs = gamestarts.run(self.root1, self.root2)

    def _find(self, entity_key: str):
        matches = [o for o in self.outputs if o.extras.get('entity_key') == entity_key]
        self.assertEqual(len(matches), 1,
                         msg=f'expected 1 match for {entity_key}, got {len(matches)}: '
                             f'{[o.text for o in matches]}')
        return matches[0]

    # Case 1 — added entity
    def test_added_gamestart(self):
        out = self._find('gs_test_added')
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['classifications'], ['tutorial', 'nosave'])
        self.assertIn('NEW', out.text)
        self.assertIn('Test Added Gamestart', out.text)
        self.assertIn('(tutorial, nosave)', out.text)
        self.assertIn('[core]', out.text)
        self.assertEqual(out.extras['new_sources'], ['core'])

    # Case 2 — removed entity
    def test_removed_gamestart(self):
        out = self._find('gs_test_removed')
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertIn('REMOVED', out.text)
        self.assertIn('Test Removed Gamestart', out.text)
        self.assertEqual(out.extras['old_sources'], ['core'])

    # Case 3 — modified with root attrs + nested singleton diffs
    def test_modified_root_and_nested(self):
        out = self._find('gs_test_modified')
        self.assertEqual(out.extras['kind'], 'modified')
        # Root @image diff.
        self.assertIn('image gamestart_mod→gamestart_mod_v2', out.text)
        # Nested cutscene attrs.
        self.assertIn('cutscene.ref scenario_mod_v1→scenario_mod_v2', out.text)
        self.assertIn('cutscene.voice voice_mod_v1→voice_mod_v2', out.text)
        # Nested player attrs.
        self.assertIn(
            'player.macro character_player_scenario_combat_argon_macro→'
            'character_player_scenario_combat_teladi_macro',
            out.text,
        )
        self.assertIn('player.money 0→5000', out.text)
        # Nested player/ship attr.
        self.assertIn(
            'player.ship.macro ship_arg_s_fighter_04_a_macro→'
            'ship_tel_s_fighter_01_a_macro',
            out.text,
        )
        # Universe attrs (the `@ventures` change arrives from split DLC, the
        # core file has `ventures="false"` unchanged; after DLC overlay the
        # effective v2 value is "true").
        self.assertIn('universe.ventures false→true', out.text)

    # Case 4 — tag flip (classification change on modified entity)
    def test_tagflip_classification_changed(self):
        out = self._find('gs_test_tagflip')
        self.assertEqual(out.extras['kind'], 'modified')
        # New side classifications should include both tokens.
        self.assertEqual(out.extras['classifications'], ['tutorial', 'nosave'])
        # The `@tags` diff should surface as a root-attr change.
        self.assertIn('tags tutorial→tutorial nosave', out.text)

    # Case 5 — DLC-sourced new entity
    def test_dlc_sourced_added(self):
        out = self._find('gs_test_dlc_sourced')
        self.assertEqual(out.extras['kind'], 'added')
        self.assertIn('split', out.extras['new_sources'])
        self.assertNotIn('core', out.extras['new_sources'])
        self.assertIn('[split]', out.text)
        self.assertIn('Test DLC Sourced Gamestart', out.text)

    # Case 6 — provenance handoff (core v1 → core+split v2)
    def test_modified_provenance_handoff(self):
        out = self._find('gs_test_modified')
        self.assertEqual(out.extras['old_sources'], ['core'])
        self.assertEqual(sorted(out.extras['new_sources']), ['core', 'split'])
        self.assertIn('[core→core+split]', out.text)

    # Case 7 — incomplete sentinel from timelines DLC's bad xpath
    def test_incomplete_sentinel_present(self):
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete']
        self.assertEqual(
            len(sentinels), 1,
            msg=f'expected 1 incomplete sentinel, got {len(sentinels)}',
        )
        self.assertIn('RULE INCOMPLETE', sentinels[0].text)
        self.assertTrue(sentinels[0].extras.get('incomplete'))

    # Case 8 — positional overlap warning from boron + terran
    def test_warning_positional_overlap(self):
        warnings = [o for o in self.outputs if o.extras.get('kind') == 'warning']
        overlaps = [w for w in warnings if 'positional overlap' in w.text]
        self.assertTrue(
            overlaps,
            msg=f'expected positional overlap warning; got {[w.text for w in warnings]}',
        )

    # Case 9 — unchanged gamestart emits nothing
    def test_unchanged_gamestart_no_output(self):
        matches = [o for o in self.outputs
                   if o.extras.get('entity_key') == 'gs_test_unchanged']
        self.assertEqual(
            matches, [],
            msg=f'unchanged gamestart should emit nothing; got {[o.text for o in matches]}',
        )


if __name__ == '__main__':
    unittest.main()
