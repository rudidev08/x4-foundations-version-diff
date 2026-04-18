"""Unit tests for the `factions` rule.

Covers the standard 9-case matrix across the two sub-sources
(faction / action): added, removed, modified, DLC-sourced, provenance
handoff, incomplete cases (duplicate licence @type, duplicate param
@name, unenumerated action child tag), warnings, and unchanged.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.paths import reset_index
from src.rules import factions


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'factions' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'factions' / 'TEST-2.00'


class FactionsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = factions.run(ROOT1, ROOT2)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]

    # ---------- Case 1: Added (DLC-sourced faction) ----------
    def test_added_boron_faction_from_dlc(self):
        """boron faction is added via boron DLC in TEST-2.00."""
        matches = self._find(('faction', 'boron'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'faction')
        self.assertIn('NEW', out.text)
        self.assertIn('Boron Kingdom', out.text)
        self.assertIn('[boron]', out.text)
        # Classifications: [primaryrace, behaviourset] with 'faction' stripped.
        self.assertIn('boron', out.extras['classifications'])
        self.assertIn('default', out.extras['classifications'])
        self.assertNotIn('faction', out.extras['classifications'])

    # ---------- Case 2: Removed (core faction) ----------
    def test_removed_terran_faction(self):
        """terran faction is in TEST-1.00 only."""
        matches = self._find(('faction', 'terran'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'faction')
        self.assertIn('REMOVED', out.text)
        self.assertIn('Terran Protectorate', out.text)

    # ---------- Case 3: Modified faction (attrs + licences + relations) ----------
    def test_modified_argon_faction(self):
        """argon faction has policefaction change, new licence, and relation
        changes."""
        matches = self._find(('faction', 'argon'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'faction')
        # Attr change.
        self.assertIn('policefaction argon→argonpolice', out.text)
        # Licence added.
        self.assertIn('licence[type=station_gen_basic] added', out.text)
        # Relation change and add.
        self.assertIn('relation[faction=khaak] -1→-0.5', out.text)
        self.assertIn('relation[faction=scaleplate] added=-0.1', out.text)
        # Display + classifications.
        self.assertIn('Argon Federation', out.text)

    # ---------- Case 4: Licence duplicate composite key — uniqueness incomplete ----------
    def test_licence_type_not_unique_incomplete(self):
        """badguys has two <licence @type='capitalship'> entries, both with
        no @factions attribute — composite (type, factions)=(capitalship,
        None) collides and should trigger the uniqueness assertion."""
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete'
                     and o.extras.get('subsource') == 'faction']
        self.assertTrue(sentinels,
                        msg=f'no faction-sub-source incomplete sentinel in '
                            f'{[o.text for o in self.outputs]}')
        sentinel = sentinels[0]
        reasons = [f[1].get('reason') for f in sentinel.extras['failures']]
        self.assertIn('licence_type_not_unique', reasons,
                      msg=f'reasons={reasons}')
        # The affected_keys must name the badguys faction.
        aks = [f[1].get('affected_keys') for f in sentinel.extras['failures']
               if f[1].get('reason') == 'licence_type_not_unique']
        self.assertTrue(
            any(('faction', 'badguys') in (ak or []) for ak in aks),
            msg=f'affected_keys={aks}',
        )

    # ---------- Case 5: Action added with nested <cost><ware> ----------
    def test_added_improve_relations_medium(self):
        """improve_relations_medium is new in TEST-2.00; has nested ware
        entries that the keyed-by-@ware matcher should handle on the add
        side via the action row's classifications."""
        matches = self._find(('action', 'improve_relations_medium'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'action')
        self.assertIn('NEW', out.text)
        self.assertIn('Improve Relations (Medium)', out.text)
        # Classifications: [@category] with 'action' stripped.
        self.assertIn('negotiation', out.extras['classifications'])
        self.assertNotIn('action', out.extras['classifications'])

    # ---------- Case 6: Action removed ----------
    def test_removed_deprecated_action(self):
        """deprecated_action is in TEST-1.00 only."""
        matches = self._find(('action', 'deprecated_action'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'action')
        self.assertIn('REMOVED', out.text)

    # ---------- Case 7: Action modified — singletons + params ----------
    def test_modified_cultivate_influence(self):
        """cultivate_influence has <cost money>, <reward influence>, <time>,
        and <params>/<param>/<input_param> changes."""
        matches = self._find(('action', 'cultivate_influence'))
        self.assertEqual(len(matches), 1, msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'action')
        # Cost root attr change.
        self.assertIn('cost money 5000→6500', out.text)
        # Reward root attr change.
        self.assertIn('reward influence 4→5', out.text)
        # Time singleton attr change.
        self.assertIn('time duration 900→1200', out.text)
        # Params nested input_param value change.
        self.assertIn('params.param[station].input_param[stationtype] value',
                      out.text)
        # An input_param was renamed / removed / added.
        self.assertIn('params.param[station].input_param[inrelationrange] removed',
                      out.text)
        self.assertIn('params.param[station].input_param[notinrelationrange] added',
                      out.text)

    # ---------- Case 8: Param name not unique incomplete ----------
    def test_param_name_not_unique_incomplete(self):
        """bad_params has duplicate <param @name='station'> in TEST-2.00."""
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete'
                     and o.extras.get('subsource') == 'action']
        self.assertTrue(sentinels,
                        msg=f'no action-sub-source incomplete sentinel in '
                            f'{[o.text for o in self.outputs]}')
        sentinel = sentinels[0]
        reasons = [f[1].get('reason') for f in sentinel.extras['failures']]
        self.assertIn('param_name_not_unique', reasons, msg=f'reasons={reasons}')
        # The affected_keys must name bad_params specifically.
        aks = [f[1].get('affected_keys') for f in sentinel.extras['failures']
               if f[1].get('reason') == 'param_name_not_unique']
        self.assertTrue(
            any(('action', 'bad_params') in (ak or []) for ak in aks),
            msg=f'affected_keys={aks}',
        )
        # bad_params row must be flagged incomplete on the modified side.
        bad_params = self._find(('action', 'bad_params'))
        self.assertEqual(len(bad_params), 1)
        self.assertTrue(bad_params[0].extras.get('incomplete'),
                        msg=f'bad_params row not incomplete: {bad_params[0].text}')

    # ---------- Case 9: Unenumerated action child tag incomplete ----------
    def test_unhandled_action_child_tag_incomplete(self):
        """weirdchild has a <weirdtag> direct child that no matcher handles."""
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete'
                     and o.extras.get('subsource') == 'action']
        self.assertTrue(sentinels)
        sentinel = sentinels[0]
        reasons = [f[1].get('reason') for f in sentinel.extras['failures']]
        self.assertIn('no_child_matcher', reasons, msg=f'reasons={reasons}')
        # Locate the specific failure entry.
        matching = [f for f in sentinel.extras['failures']
                    if f[1].get('reason') == 'no_child_matcher']
        self.assertTrue(matching)
        self.assertEqual(matching[0][1].get('subtree'), 'weirdtag')
        # The weirdchild action row itself must be marked incomplete.
        weirdchild = self._find(('action', 'weirdchild'))
        self.assertEqual(len(weirdchild), 1)
        self.assertTrue(weirdchild[0].extras.get('incomplete'),
                        msg=f'weirdchild row not marked incomplete: '
                            f'{weirdchild[0].text}')

    # ---------- Case: Unchanged faction emits nothing ----------
    def test_unchanged_badguys_emits_no_modified_row(self):
        """badguys has no actual diff; no modified row should appear.

        The uniqueness check still generates a sentinel (see duplicate-type
        test above) but that's a diagnostic, not a per-faction content row.
        """
        matches = self._find(('faction', 'badguys'))
        # Either no row (no diffs) or it was emitted as kind=modified with
        # no real changes — the rule returns [] in the latter case, so the
        # match set should be empty.
        self.assertEqual(matches, [],
                         msg=f'unexpected row(s): {[m.text for m in matches]}')

    # ---------- Sanity: rule produces outputs ----------
    def test_run_does_not_crash(self):
        self.assertTrue(self.outputs)


class FactionsHelpersTest(unittest.TestCase):
    """Direct tests for the action child-tag matcher and classification."""

    def test_faction_classifications_strips_generic_token(self):
        import xml.etree.ElementTree as ET
        e = ET.fromstring(
            '<faction id="x" primaryrace="argon" behaviourset="default" />'
        )
        self.assertEqual(
            factions._faction_classifications(e),
            ['argon', 'default'],
        )

    def test_action_classifications_strips_generic_token(self):
        import xml.etree.ElementTree as ET
        e = ET.fromstring('<action id="x" category="negotiation" />')
        self.assertEqual(
            factions._action_classifications(e),
            ['negotiation'],
        )

    def test_action_known_children_is_the_documented_set(self):
        self.assertEqual(
            factions.ACTION_KNOWN_CHILDREN,
            frozenset({'cost', 'reward', 'params',
                       'time', 'icon', 'success', 'failure', 'agent'}),
        )


if __name__ == '__main__':
    unittest.main()
