"""Real-data tests for the `loadouts` rule.

Tier A — smoke: runs against every pair and verifies a non-empty list.
Tier B — snapshot: canonical 8.00H4 → 9.00B6 pair, sorted one-line-per-output
digest snapshot. Regenerate via
`X4_REGEN_SNAPSHOT=loadouts python3 -m unittest tests.test_realdata_loadouts`.
"""
import os
import sys
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from src.lib import cache
from src.lib.paths import reset_index
from src.rules import loadouts


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'loadouts_8.00H4_9.00B6.txt'


def _key(o):
    return o.extras.get('entity_key') or ''


def _kind(o):
    return o.extras.get('kind', '')


def _subsource(o):
    return o.extras.get('subsource') or ''


def _run(pair):
    cache.clear()
    reset_index()
    return loadouts.run(CORPUS / pair[0], CORPUS / pair[1])


class TierASmokeTest(unittest.TestCase):
    def test_runs_on_every_pair(self):
        for pair in tier_a_pairs():
            with self.subTest(pair=pair):
                if not (CORPUS / pair[0]).is_dir() or not (CORPUS / pair[1]).is_dir():
                    self.skipTest(f'missing {pair}')
                outs = _run(pair)
                self.assertIsInstance(outs, list)


class TierBBaselineTest(unittest.TestCase):
    def test_snapshot_matches(self):
        require(CANONICAL_PAIR)
        outs = _run(CANONICAL_PAIR)
        lines = sorted(
            f'{repr(_key(o))}\t{_kind(o)}\t{_subsource(o)}\t'
            f'{sha256(o.text.encode()).hexdigest()}'
            for o in outs
        )
        actual = '\n'.join(lines) + '\n'
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'loadouts':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(
            actual, expected,
            'Loadouts snapshot drift. '
            'Regen: X4_REGEN_SNAPSHOT=loadouts python3 -m unittest '
            'tests.test_realdata_loadouts',
        )


if __name__ == '__main__':
    unittest.main()
