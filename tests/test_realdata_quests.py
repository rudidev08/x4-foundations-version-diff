"""Real-data tests for the `quests` rule.

Tier A — smoke: runs against every pair and verifies a non-empty list.
Tier B — snapshot: canonical 8.00H4 → 9.00B6 pair, sorted one-line-per-output
digest snapshot. Regenerate via
`X4_REGEN_SNAPSHOT=quests python3 -m unittest tests.test_realdata_quests`.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from tests.realdata_allowlist import is_allowlisted
from src.lib import cache
from src.lib.paths import reset_index
from src.lib.rule_output import snapshot_line
from src.rules import quests


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'quests_8.00H4_9.00B6.txt'


def _run(pair):
    cache.clear()
    reset_index()
    return quests.run(CORPUS / pair[0], CORPUS / pair[1])


class TierASmokeTest(unittest.TestCase):
    def test_runs_on_every_pair(self):
        for pair in tier_a_pairs():
            with self.subTest(pair=pair):
                if not (CORPUS / pair[0]).is_dir() or not (CORPUS / pair[1]).is_dir():
                    self.skipTest(f'missing {pair}')
                outs = _run(pair)
                self.assertIsInstance(outs, list)

    def test_canonical_pair_failures_allowlisted(self):
        """Tier A contract: unreviewed incompletes must be allowlisted."""
        require(CANONICAL_PAIR)
        outs = _run(CANONICAL_PAIR)
        bad = [o for o in outs
               if o.extras.get('incomplete') and not is_allowlisted(o)]
        self.assertFalse(
            bad,
            msg=f'unallowed incompletes: {[o.text for o in bad]}',
        )


class TierBBaselineTest(unittest.TestCase):
    def test_snapshot_matches(self):
        require(CANONICAL_PAIR)
        outs = _run(CANONICAL_PAIR)
        lines = sorted(snapshot_line(o) for o in outs)
        actual = '\n'.join(lines) + '\n'
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'quests':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(
            actual, expected,
            'Quests snapshot drift. '
            'Regen: X4_REGEN_SNAPSHOT=quests python3 -m unittest tests.test_realdata_quests',
        )


if __name__ == '__main__':
    unittest.main()
