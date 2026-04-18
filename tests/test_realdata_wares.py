import os
import sys
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from tests.realdata_allowlist import is_allowlisted
from src.lib import cache
from src.lib.rule_output import snapshot_line
from src.rules import wares


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'wares_8.00H4_9.00B6.txt'


BASELINE = {
    'pair': CANONICAL_PAIR,
    'sentinels': [
        # Real 8.00H4→9.00B6 ware diffs — 9.00 shipped khaak scrap salvage
        # wares (new) and re-priced inventory/missilecomponents wares (modified).
        {'entity_key': 'khaakalloy', 'kind': 'added'},
        {'entity_key': 'missilecomponents', 'kind': 'modified'},
    ],
}


def _run(pair):
    cache.clear()
    return wares.run(CORPUS / pair[0], CORPUS / pair[1])


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
        self.assertFalse(bad,
                         msg=f'unallowed incompletes: {[o.text for o in bad]}')


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
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'wares':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(actual, expected,
                         'Wares snapshot drift. '
                         'Regen: X4_REGEN_SNAPSHOT=wares python3 -m unittest tests.test_realdata_wares')


if __name__ == '__main__':
    unittest.main()
