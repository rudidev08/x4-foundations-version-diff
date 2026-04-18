import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from tests.realdata_allowlist import is_allowlisted
from src.lib import cache
from src.lib.rule_output import snapshot_line
from src.rules import jobs


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'jobs_8.00H4_9.00B6.txt'


BASELINE = {
    'pair': CANONICAL_PAIR,
    'sentinels': [
        # Real 8.00H4→9.00B6 job diffs. 9.00 introduced antigone_* and
        # argon_*_phase2 job families (additive). Pick one known-added job
        # id and one known-modified core job id as presence sentinels.
        {'entity_key': 'antigone_hullparts_trader_l', 'kind': 'added'},
        {'entity_key': 'paranid_corvette_patrol_m_sector', 'kind': 'modified'},
    ],
}


def _run(pair):
    cache.clear()
    return jobs.run(CORPUS / pair[0], CORPUS / pair[1])


class TierASmokeTest(unittest.TestCase):
    def test_runs_on_every_pair(self):
        for pair in tier_a_pairs():
            with self.subTest(pair=pair):
                if not (CORPUS / pair[0]).is_dir() or not (CORPUS / pair[1]).is_dir():
                    self.skipTest(f'missing {pair}')
                outs = _run(pair)
                self.assertIsInstance(outs, list)

    def test_canonical_pair_failures_allowlisted(self):
        """Tier A contract: unreviewed incompletes must be allowlisted.

        Spec: 'no silent changes' — any output with extras.incomplete=True
        not on the allowlist is a gate failure.
        """
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        bad = [o for o in outs
               if o.extras.get('incomplete') and not is_allowlisted(o)]
        self.assertFalse(
            bad,
            msg=f'unallowed incompletes: {[o.text for o in bad[:10]]} '
                f'({len(bad)} total)'
        )


class TierBBaselineTest(unittest.TestCase):
    def test_sentinels_present(self):
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        for s in BASELINE['sentinels']:
            match = [o for o in outs
                     if o.extras.get('entity_key') == s['entity_key']
                     and o.extras.get('kind') == s['kind']]
            self.assertTrue(match, f'missing sentinel {s}')

    def test_snapshot_matches(self):
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        lines = sorted(snapshot_line(o) for o in outs)
        actual = '\n'.join(lines) + '\n'
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'jobs':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(
            actual, expected,
            'Jobs snapshot drift. '
            'Regen: X4_REGEN_SNAPSHOT=jobs python3 -m unittest tests.test_realdata_jobs'
        )


if __name__ == '__main__':
    unittest.main()
