import os
import sys
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from src import change_map
from src.rules import shields


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'shields_8.00H4_9.00B6.txt'


def _shim_key(out):
    return out.extras.get('macro') or ''


def _shim_kind(out):
    if 'REMOVED' in out.text:
        return 'removed'
    if 'NEW' in out.text:
        return 'added'
    return 'modified'


BASELINE = {
    'pair': CANONICAL_PAIR,
    'expected_output_count': None,
    'sentinels': [
        {'entity_key_contains': 'shield_tel_m_standard_01_mk1'},
    ],
}


def _run(pair):
    old, new = CORPUS / pair[0], CORPUS / pair[1]
    chs = change_map.build(old, new)
    return shields.run(old, new, chs)


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
        for sentinel in BASELINE['sentinels']:
            needle = sentinel['entity_key_contains']
            self.assertTrue(any(needle in _shim_key(o) for o in outs),
                            f'missing sentinel: {needle}')

    def test_snapshot_matches(self):
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        lines = sorted(
            f'{_shim_key(o)}\t{_shim_kind(o)}\t{sha256(o.text.encode()).hexdigest()}'
            for o in outs
        )
        actual = '\n'.join(lines) + '\n'
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'shields':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(actual, expected,
                         'Shields snapshot drift. '
                         'Regen: X4_REGEN_SNAPSHOT=shields python3 -m unittest tests.test_realdata_shields')


if __name__ == '__main__':
    unittest.main()
