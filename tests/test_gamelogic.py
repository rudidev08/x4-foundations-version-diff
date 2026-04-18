"""Unit tests for the `gamelogic` rule.

Covers the 9-case matrix across the three sub-sources (aiscript / behaviour /
scriptproperty): added, removed, modified (including DLC-patched aiscript),
unchanged, incomplete (unhandled behaviour child tag + unhandled
scriptproperty child tag), classifications/prefix mapping, and
composite-tuple entity_keys.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.paths import reset_index
from src.rules import gamelogic


HERE = Path(__file__).resolve().parent
ROOT1 = HERE / 'fixtures' / 'gamelogic' / 'TEST-1.00'
ROOT2 = HERE / 'fixtures' / 'gamelogic' / 'TEST-2.00'


class GamelogicRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cache.clear()
        reset_index()
        cls.outputs = gamelogic.run(ROOT1, ROOT2)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]

    # ---------- Case 1: aiscript modified + DLC patch applied ----------
    def test_aiscript_modified_reflects_dlc_patch(self):
        """fight_escape differs in TEST-2.00 core AND gets a boron DLC patch.

        The effective-script diff must reflect BOTH changes (not the raw
        patch-XML bytes): the core bump from 1s→2s composed with the DLC
        replace to 3s, plus the DLC-added boron_specific param.
        """
        matches = self._find(('aiscript', 'fight_escape'))
        self.assertEqual(len(matches), 1,
                         msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'aiscript')
        # Classifications include `aiscript` and `fight` (prefix token).
        self.assertIn('aiscript', out.extras['classifications'])
        self.assertIn('fight', out.extras['classifications'])
        # Sources reflect DLC contribution.
        self.assertIn('boron', out.extras.get('new_sources', []))
        self.assertIn('core', out.extras.get('new_sources', []))
        # Diff body shows the effective-script content delta.
        diff = out.extras.get('diff', '')
        # DLC patch replaced wait exact to 3s.
        self.assertIn('exact="3s"', diff, msg=f'diff={diff}')
        # DLC patch added boron_specific param.
        self.assertIn('boron_specific', diff, msg=f'diff={diff}')
        # Core-side update: new escort param.
        self.assertIn('escort', diff)

    def test_aiscript_emits_one_output_per_filename(self):
        """fight_escape core + DLC patch must collapse into ONE output keyed
        by filename, not two (one for core, one for DLC)."""
        matches = self._find(('aiscript', 'fight_escape'))
        self.assertEqual(len(matches), 1)

    # ---------- Case 2: aiscript added ----------
    def test_aiscript_added(self):
        """order.move.generic is new in TEST-2.00."""
        matches = self._find(('aiscript', 'order.move.generic'))
        self.assertEqual(len(matches), 1,
                         msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'aiscript')
        self.assertIn('ADDED', out.text)
        self.assertIn('order.move.generic', out.text)
        # Prefix classification = 'order'.
        self.assertIn('order', out.extras['classifications'])

    # ---------- Case 3: aiscript removed ----------
    def test_aiscript_removed(self):
        """trade_buy is in TEST-1.00 only."""
        matches = self._find(('aiscript', 'trade_buy'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'aiscript')
        self.assertIn('REMOVED', out.text)
        self.assertIn('trade', out.extras['classifications'])

    # ---------- Case 4: behaviour modified ----------
    def test_behaviour_modified(self):
        """dogfight1 chance changed 60→75."""
        matches = self._find(
            ('behaviour', ('default', 'normal', 'dogfight1')),
        )
        self.assertEqual(len(matches), 1,
                         msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'behaviour')
        self.assertIn('chance 60→75', out.text)
        self.assertEqual(out.extras['classifications'],
                         ['behaviour', 'default', 'normal'])

    # ---------- Case 5: behaviour added ----------
    def test_behaviour_added(self):
        """new_evasive is added in TEST-2.00."""
        matches = self._find(
            ('behaviour', ('default', 'evade', 'new_evasive')),
        )
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['subsource'], 'behaviour')
        self.assertIn('NEW', out.text)
        self.assertIn('chance=45', out.text)
        self.assertEqual(out.extras['classifications'],
                         ['behaviour', 'default', 'evade'])

    # ---------- Case 6: behaviour removed ----------
    def test_behaviour_removed(self):
        """old_chase is in TEST-1.00 only."""
        matches = self._find(
            ('behaviour', ('default', 'normal', 'old_chase')),
        )
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertEqual(out.extras['subsource'], 'behaviour')
        self.assertIn('REMOVED', out.text)

    # ---------- Case 7: scriptproperty modified ----------
    def test_scriptproperty_modified(self):
        """component.exists has result changed."""
        matches = self._find(('scriptproperty', ('component', 'exists')))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertEqual(out.extras['subsource'], 'scriptproperty')
        self.assertIn('component.exists', out.text)
        self.assertIn('result', out.text)
        # Extras carry result field.
        self.assertEqual(out.extras['result'], 'true iff it is present')

    # ---------- Case 8: scriptproperty added + removed ----------
    def test_scriptproperty_added(self):
        matches = self._find(('scriptproperty', ('component', 'new_prop')))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'added')
        self.assertIn('result=new property', matches[0].text)

    def test_scriptproperty_removed(self):
        matches = self._find(
            ('scriptproperty', ('component', 'deprecated_prop')),
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')

    # ---------- Case 9: unhandled child tags flag incomplete ----------
    def test_behaviour_unhandled_child_tag_incomplete(self):
        """weirdchild has <unexpectedtag> — must mark behaviour row incomplete
        and emit a subsource=behaviour sentinel."""
        weirdchild = self._find(
            ('behaviour', ('default', 'normal', 'weirdchild')),
        )
        self.assertEqual(len(weirdchild), 1)
        self.assertTrue(weirdchild[0].extras.get('incomplete'),
                        msg=f'weirdchild row not incomplete: '
                            f'{weirdchild[0].text}')
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete'
                     and o.extras.get('subsource') == 'behaviour']
        self.assertTrue(sentinels)
        reasons = [f[1].get('reason') for f in sentinels[0].extras['failures']]
        self.assertIn('unhandled_child_tag', reasons)

    def test_scriptproperty_unhandled_child_tag_incomplete(self):
        """weird_prop has <unknowntag> — same pattern, subsource=scriptproperty."""
        weird = self._find(('scriptproperty', ('component', 'weird_prop')))
        self.assertEqual(len(weird), 1)
        self.assertTrue(weird[0].extras.get('incomplete'))
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete'
                     and o.extras.get('subsource') == 'scriptproperty']
        self.assertTrue(sentinels)
        reasons = [f[1].get('reason') for f in sentinels[0].extras['failures']]
        self.assertIn('unhandled_child_tag', reasons)

    # ---------- Param diff on scriptproperty with children ----------
    def test_scriptproperty_example_added_multiset(self):
        """object.isclass.{$class} gets a new <example> child — multiset diff
        emits an `example added` label."""
        matches = self._find(
            ('scriptproperty', ('object', 'isclass.{$class}')),
        )
        self.assertEqual(len(matches), 1,
                         msg=[o.text for o in self.outputs])
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('example added', out.text)
        self.assertIn('value=ship', out.text)

    # ---------- Sanity: rule produces outputs ----------
    def test_run_does_not_crash(self):
        self.assertTrue(self.outputs)

    # ---------- Contamination bridge: composite tuple keys ----------
    def test_composite_tuple_entity_keys(self):
        """Every behaviour + scriptproperty entity_key must be a tuple of
        `(subsource, composite_inner)` — never a bare string."""
        for o in self.outputs:
            sub = o.extras.get('subsource')
            if sub not in ('behaviour', 'scriptproperty'):
                continue
            ek = o.extras.get('entity_key')
            # Skip incomplete sentinels (diagnostic entity_key shape).
            if o.extras.get('kind') == 'incomplete':
                continue
            self.assertIsInstance(ek, tuple,
                                  msg=f'entity_key not tuple: {ek}')
            self.assertEqual(ek[0], sub,
                             msg=f'subsource prefix mismatch: {ek}')
            self.assertIsInstance(ek[1], tuple,
                                  msg=f'inner key not tuple: {ek}')


class GamelogicHelpersTest(unittest.TestCase):
    """Direct tests for the aiscript prefix mapping logic."""

    def test_aiscript_prefix_fight(self):
        self.assertEqual(gamelogic._aiscript_prefix('fight_escape.xml'),
                         'fight')

    def test_aiscript_prefix_interrupt_dot(self):
        """interrupt.attacked.xml uses '.' as first separator."""
        self.assertEqual(
            gamelogic._aiscript_prefix('interrupt.attacked.xml'),
            'interrupt',
        )

    def test_aiscript_prefix_interrupt_underscore(self):
        self.assertEqual(
            gamelogic._aiscript_prefix('interrupt_foo.xml'),
            'interrupt',
        )

    def test_aiscript_prefix_order_dot(self):
        self.assertEqual(
            gamelogic._aiscript_prefix('order.move.patrol.xml'),
            'order',
        )

    def test_aiscript_prefix_unknown_token_returns_none(self):
        self.assertIsNone(gamelogic._aiscript_prefix('lib.helpers.xml'))
        self.assertIsNone(gamelogic._aiscript_prefix('engineer.ai.xml'))

    def test_aiscript_prefix_empty_filename(self):
        self.assertIsNone(gamelogic._aiscript_prefix(''))

    def test_behaviour_child_whitelist(self):
        """Whitelist is {'param', 'precondition', 'script'} — stability
        contract."""
        self.assertEqual(
            gamelogic._BEHAVIOUR_CHILD_TAGS,
            frozenset({'param', 'precondition', 'script'}),
        )

    def test_scriptproperty_child_whitelist(self):
        """Whitelist is {'param', 'example'}."""
        self.assertEqual(
            gamelogic._SCRIPTPROPERTY_CHILD_TAGS,
            frozenset({'param', 'example'}),
        )


if __name__ == '__main__':
    unittest.main()
