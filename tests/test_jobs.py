import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.rules import jobs


HERE = Path(__file__).resolve().parent


class JobsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root1 = HERE / 'fixtures' / 'jobs' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'jobs' / 'TEST-2.00'

    def setUp(self):
        cache.clear()
        self.outputs = jobs.run(self.root1, self.root2)

    def _find(self, entity_key: str):
        matches = [o for o in self.outputs if o.extras.get('entity_key') == entity_key]
        self.assertEqual(len(matches), 1,
                         msg=f'expected 1 match for {entity_key}, got {len(matches)}: '
                             f'{[o.text for o in matches]}')
        return matches[0]

    # Case 1 — added entity
    def test_teladi_miner_added(self):
        out = self._find('job_tel_miner_01')
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(
            out.extras['classifications'],
            ['teladi', 'mining', 'medium', 'ship_m'],
        )
        self.assertIn('NEW', out.text)
        self.assertIn('Test Teladi Miner', out.text)
        self.assertIn('(teladi, mining, medium, ship_m)', out.text)
        self.assertIn('[core]', out.text)
        self.assertEqual(out.extras['new_sources'], ['core'])

    # Case 2 — removed entity
    def test_paranid_scout_removed(self):
        out = self._find('job_par_scout_01')
        self.assertEqual(out.extras['kind'], 'removed')
        self.assertIn('REMOVED', out.text)
        self.assertIn('Test Paranid Scout', out.text)
        self.assertEqual(out.extras['old_sources'], ['core'])

    # Case 3 — modified job with @friendgroup flip, nested
    # <environment @buildatshipyard> change, and <modifiers @speedfactor>.
    def test_argon_transport_modified_friendgroup_and_modifiers(self):
        out = self._find('job_arg_transport_01')
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('friendgroup argon→argongovernment', out.text)
        self.assertIn(
            'environment.buildatshipyard argon_shipyard→argon_shipyard_v2',
            out.text,
        )
        self.assertIn('modifiers.speedfactor 1.0→1.5', out.text)

    # Case 4 — @startactive="false" lifecycle
    def test_startactive_lifecycle(self):
        out = self._find('job_bor_trade_01')
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('DEPRECATED (startactive=false)', out.text)
        # Lifecycle token must be prepended (first change in the list).
        self.assertTrue(
            out.text.split(': ', 1)[1].startswith('DEPRECATED'),
            msg=f'DEPRECATED must be first change: {out.text}',
        )

    # Case 5 — DLC-sourced
    def test_split_fighter_dlc_sourced(self):
        out = self._find('job_spl_fighter_01')
        self.assertEqual(out.extras['kind'], 'added')
        self.assertIn('split', out.extras['new_sources'])
        self.assertNotIn('core', out.extras['new_sources'])
        self.assertIn('[split]', out.text)
        self.assertIn('Test Split Fighter', out.text)

    # Case 6 — provenance handoff (core v1 → core+split v2)
    def test_argon_transport_provenance_handoff(self):
        out = self._find('job_arg_transport_01')
        self.assertEqual(out.extras['old_sources'], ['core'])
        self.assertEqual(sorted(out.extras['new_sources']), ['core', 'split'])
        self.assertIn('[core→core+split]', out.text)

    # Case 7 — incomplete sentinel forwarded from diff_library patch failures
    def test_incomplete_sentinel_present(self):
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete']
        self.assertEqual(
            len(sentinels), 1,
            msg=f'expected 1 incomplete sentinel, got {len(sentinels)}',
        )
        self.assertIn('RULE INCOMPLETE', sentinels[0].text)
        self.assertTrue(sentinels[0].extras.get('incomplete'))

    # Case 8 — positional overlap warning from two DLCs
    def test_warning_positional_overlap(self):
        warnings = [o for o in self.outputs if o.extras.get('kind') == 'warning']
        overlaps = [w for w in warnings if 'positional overlap' in w.text]
        self.assertTrue(
            overlaps,
            msg=f'expected positional overlap warning; got {[w.text for w in warnings]}',
        )

    # Case 9 — unchanged job emits no output
    def test_unchanged_job_no_output(self):
        matches = [o for o in self.outputs
                   if o.extras.get('entity_key') == 'job_test_unchanged']
        self.assertEqual(
            matches, [],
            msg=f'unchanged job should emit nothing; got {[o.text for o in matches]}',
        )

    # Rule-level: unhandled direct-child tag marks affected job incomplete
    def test_unhandled_child_tag_incomplete(self):
        out = self._find('job_test_bad_child')
        self.assertTrue(
            out.extras.get('incomplete'),
            msg=f'job_test_bad_child must be incomplete due to <weirdnewtag>: {out.text}',
        )
        # The sentinel's failures list should carry the unhandled-tag reason.
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete']
        reasons = set()
        for s in sentinels:
            for _text, extras in (s.extras.get('failures') or []):
                r = extras.get('reason')
                if r:
                    reasons.add(r)
        self.assertIn('unhandled_child_tag', reasons)

    # Rule-level: repeated singleton child marks affected job incomplete
    def test_repeated_singleton_incomplete(self):
        out = self._find('job_test_repeated_singleton')
        self.assertTrue(
            out.extras.get('incomplete'),
            msg=f'job_test_repeated_singleton must be incomplete: {out.text}',
        )
        sentinels = [o for o in self.outputs
                     if o.extras.get('kind') == 'incomplete']
        reasons = set()
        for s in sentinels:
            for _text, extras in (s.extras.get('failures') or []):
                r = extras.get('reason')
                if r:
                    reasons.add(r)
        self.assertIn('repeated_modifiers', reasons)


if __name__ == '__main__':
    unittest.main()
