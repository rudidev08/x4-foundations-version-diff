import os
import sys
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from src.rules import missiles


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'missiles_8.00H4_9.00B6.txt'


def _shim_key(out):
    return out.extras.get('ware_id') or ''


def _shim_kind(out):
    return out.extras.get('kind', '')


BASELINE = {
    'pair': CANONICAL_PAIR,
    'sentinels': [
        {'entity_key': 'missile_gen_m_guided_01_mk1', 'kind': 'added'},
        {'entity_key': 'missile_torpedo_heavy_mk1', 'kind': 'modified'},
    ],
}


def _run(pair):
    return missiles.run(CORPUS / pair[0], CORPUS / pair[1])


class TierASmokeTest(unittest.TestCase):
    def test_runs_on_every_pair(self):
        for pair in tier_a_pairs():
            with self.subTest(pair=pair):
                if not (CORPUS / pair[0]).is_dir() or not (CORPUS / pair[1]).is_dir():
                    self.skipTest(f'missing {pair}')
                outs = _run(pair)
                self.assertIsInstance(outs, list)


class TierBBaselineTest(unittest.TestCase):
    def test_sentinels_present(self):
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        for s in BASELINE['sentinels']:
            match = [o for o in outs
                     if _shim_key(o) == s['entity_key'] and _shim_kind(o) == s['kind']]
            self.assertTrue(match, f'missing sentinel {s}')

    def test_snapshot_matches(self):
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        lines = sorted(
            f'{_shim_key(o)}\t{_shim_kind(o)}\t{sha256(o.text.encode()).hexdigest()}'
            for o in outs
        )
        actual = '\n'.join(lines) + '\n'
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'missiles':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(actual, expected,
                         'Missiles snapshot drift. '
                         'Regen: X4_REGEN_SNAPSHOT=missiles python3 -m unittest tests.test_realdata_missiles')


if __name__ == '__main__':
    unittest.main()
