"""Real-data tests for the engines rule.

Tier A — smoke test: run on every pair in `tier_a_pairs()`; assert list.
Tier B — sentinels + snapshot on CANONICAL_PAIR (8.00H4 → 9.00B6).

Note on sentinels: 8.00H4 → 9.00B6 contains no engine ware-level changes and
no macro-stat changes inside the diffable fields (differences are limited to
`<component ref>` swaps and `<effects>` refs, neither of which is in WARE_STATS
or MACRO_STATS). The only output is the `incomplete` sentinel from cross-DLC
patch failures in other library files. The snapshot captures that deterministic
output; the `require_incomplete_sentinel` assertion verifies the rule surfaces
the expected diagnostic when upstream materialization has failures.
"""
import os
import sys
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from src.lib import cache
from src.rules import engines


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'engines_8.00H4_9.00B6.txt'


def _shim_key(out):
    return out.extras.get('entity_key')


def _shim_kind(out):
    return out.extras.get('kind', '')


BASELINE = {
    'pair': CANONICAL_PAIR,
    # 8.00H4 → 9.00B6 has no engine entity-level outputs; the incomplete
    # sentinel (from unrelated cross-DLC wares.xml patch failures) is what
    # surfaces. The realdata test just requires that diagnostic to appear.
    'require_incomplete_sentinel': True,
}


def _run(pair):
    cache.clear()
    return engines.run(CORPUS / pair[0], CORPUS / pair[1])


class TierASmokeTest(unittest.TestCase):
    def test_runs_on_every_pair(self):
        for pair in tier_a_pairs():
            with self.subTest(pair=pair):
                if not (CORPUS / pair[0]).is_dir() or not (CORPUS / pair[1]).is_dir():
                    self.skipTest(f'missing {pair}')
                outs = _run(pair)
                self.assertIsInstance(outs, list)


class TierBBaselineTest(unittest.TestCase):
    def test_incomplete_sentinel_present(self):
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        incompletes = [o for o in outs if _shim_kind(o) == 'incomplete']
        self.assertTrue(
            incompletes,
            'expected incomplete sentinel from cross-DLC patch failures; got: '
            f'{[o.text[:80] for o in outs]}',
        )

    def test_snapshot_matches(self):
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        lines = sorted(
            f'{repr(_shim_key(o))}\t{_shim_kind(o)}\t{sha256(o.text.encode()).hexdigest()}'
            for o in outs
        )
        actual = '\n'.join(lines) + '\n'
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'engines':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(
            actual, expected,
            'Engines snapshot drift. '
            'Regen: X4_REGEN_SNAPSHOT=engines python3 -m unittest tests.test_realdata_engines',
        )


if __name__ == '__main__':
    unittest.main()
