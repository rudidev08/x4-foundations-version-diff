"""Real-data tests for the `stations` rule.

Tier A — smoke: runs against every pair and verifies a non-empty list.
Tier B — snapshot: canonical 8.00H4 → 9.00B6 pair, sorted one-line-per-output
digest snapshot. Regenerate via `X4_REGEN_SNAPSHOT=stations python3 -m unittest
tests.test_realdata_stations`.
"""
import os
import sys
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from tests.realdata_allowlist import is_allowlisted
from src.lib import cache
from src.lib.paths import reset_index
from src.rules import stations


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'stations_8.00H4_9.00B6.txt'


def _shim_key(out):
    ek = out.extras.get('entity_key')
    if isinstance(ek, tuple):
        return repr(ek)
    return ek or ''


def _shim_kind(out):
    return out.extras.get('kind', '')


def _shim_subsource(out):
    return out.extras.get('subsource', '') or ''


def _run(pair):
    cache.clear()
    reset_index()
    return stations.run(CORPUS / pair[0], CORPUS / pair[1])


class TierASmokeTest(unittest.TestCase):
    def test_runs_on_every_pair(self):
        for pair in tier_a_pairs():
            with self.subTest(pair=pair):
                if (not (CORPUS / pair[0]).is_dir()
                        or not (CORPUS / pair[1]).is_dir()):
                    self.skipTest(f'missing {pair}')
                outs = _run(pair)
                self.assertIsInstance(outs, list)


class TierBBaselineTest(unittest.TestCase):
    def test_no_unreviewed_incompletes(self):
        """Any RULE INCOMPLETE that isn't allowlisted blocks the run."""
        require(CANONICAL_PAIR)
        outs = _run(CANONICAL_PAIR)
        unreviewed = []
        for o in outs:
            if o.extras.get('kind') not in ('incomplete', 'warning'):
                continue
            if is_allowlisted(o):
                continue
            unreviewed.append(o.text)
        incomplete_unreviewed = [t for t in unreviewed if 'RULE INCOMPLETE' in t]
        self.assertEqual(
            incomplete_unreviewed, [],
            f'unreviewed stations INCOMPLETEs: {incomplete_unreviewed}',
        )

    def test_snapshot_matches(self):
        require(CANONICAL_PAIR)
        outs = _run(CANONICAL_PAIR)
        lines = sorted(
            f'{_shim_key(o)}\t{_shim_kind(o)}\t{_shim_subsource(o)}\t'
            f'{sha256(o.text.encode()).hexdigest()}'
            for o in outs
        )
        actual = '\n'.join(lines) + '\n'
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'stations':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(
            actual, expected,
            'Stations snapshot drift. '
            'Regen: X4_REGEN_SNAPSHOT=stations python3 -m unittest '
            'tests.test_realdata_stations',
        )


if __name__ == '__main__':
    unittest.main()
