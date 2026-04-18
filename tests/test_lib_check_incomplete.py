# tests/test_lib_check_incomplete.py
import sys
import unittest
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.rule_output import RuleOutput
from src.lib.check_incomplete import (
    assert_complete, forward_incomplete, forward_incomplete_many,
    forward_warnings, IncompleteRunError,
)


DR = namedtuple('DR', ['incomplete', 'failures', 'warnings'])


def _f(reason, affected, subsource=None):
    extras = {'affected_keys': list(affected), 'reason': reason}
    if subsource is not None:
        extras['subsource'] = subsource
    return (f'{reason}', extras)


class CheckIncompleteTest(unittest.TestCase):
    def test_assert_complete_no_incomplete(self):
        outs = [RuleOutput('x', 't', {'kind': 'added'})]
        assert_complete(outs)  # no raise

    def test_assert_complete_raises(self):
        outs = [RuleOutput('x', 't', {'kind': 'incomplete', 'incomplete': True})]
        with self.assertRaises(IncompleteRunError):
            assert_complete(outs)

    def test_forward_incomplete_appends_sentinel(self):
        report = DR(incomplete=True,
                    failures=[_f('no_target', ['ent1'])],
                    warnings=[])
        outs = [RuleOutput('x', 'normal', {'entity_key': 'ent1', 'kind': 'modified'})]
        forward_incomplete(report, outs, tag='x')
        # Contaminated row also marked
        self.assertTrue(outs[0].extras.get('incomplete'))
        # Sentinel appended at end
        self.assertEqual(len(outs), 2)
        sentinel = outs[-1]
        self.assertEqual(sentinel.extras['kind'], 'incomplete')
        self.assertTrue(sentinel.extras.get('incomplete'))
        self.assertEqual(sentinel.extras.get('failures'), report.failures)
        self.assertIn('RULE INCOMPLETE', sentinel.text)

    def test_forward_incomplete_noop_when_complete(self):
        report = DR(incomplete=False, failures=[], warnings=[])
        outs = [RuleOutput('x', 't', {'kind': 'added'})]
        forward_incomplete(report, outs, tag='x')
        self.assertEqual(len(outs), 1)
        self.assertNotIn('incomplete', outs[0].extras)

    def test_forward_incomplete_empty_affected_marks_all_from_subsource(self):
        report = DR(incomplete=True,
                    failures=[_f('unparseable_xpath', [], subsource='x')],
                    warnings=[])
        outs = [RuleOutput('x', 't1', {'entity_key': 'a', 'kind': 'added'}),
                RuleOutput('x', 't2', {'entity_key': 'b', 'kind': 'added'})]
        forward_incomplete(report, outs, tag='x')
        self.assertTrue(outs[0].extras.get('incomplete'))
        self.assertTrue(outs[1].extras.get('incomplete'))

    def test_forward_incomplete_many_scopes_by_subsource(self):
        r1 = DR(incomplete=True, failures=[_f('x', ['a'])], warnings=[])
        r2 = DR(incomplete=False, failures=[], warnings=[])
        outs = [RuleOutput('rule', 't', {'entity_key': 'a', 'subsource': 'station', 'kind': 'modified'}),
                RuleOutput('rule', 't', {'entity_key': 'b', 'subsource': 'module', 'kind': 'modified'})]
        forward_incomplete_many([(r1, 'station'), (r2, 'module')], outs, tag='rule')
        self.assertTrue(outs[0].extras.get('incomplete'))
        self.assertFalse(outs[1].extras.get('incomplete', False))

    def test_forward_warnings_appends(self):
        warnings = [('positional overlap anchor=x', {'anchor': 'x'})]
        outs = []
        forward_warnings(warnings, outs, tag='rule')
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0].extras.get('kind'), 'warning')
        self.assertTrue(outs[0].extras.get('warning'))
        self.assertIn('positional overlap', outs[0].text)


if __name__ == '__main__':
    unittest.main()
