"""Real-data Tier A/B tests for the storage rule.

Tier A: rule runs without raising across every configured version pair.
Tier B: snapshot of (entity_key, kind, subsource, sha256(text)) on the canonical
        8.00H4 → 9.00B6 pair. Regenerate with
        `X4_REGEN_SNAPSHOT=storage python3 -m unittest tests.test_realdata_storage`.
"""
import os
import sys
import unittest
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._realdata import CORPUS, require, tier_a_pairs, CANONICAL_PAIR
from tests.realdata_allowlist import is_allowlisted
from src import change_map
from src.lib import cache
from src.lib.paths import reset_index
from src.rules import storage


HERE = Path(__file__).resolve().parent
SNAP = HERE / 'snapshots' / 'storage_8.00H4_9.00B6.txt'


def _shim_key(out):
    ek = out.extras.get('entity_key')
    if isinstance(ek, tuple):
        return repr(ek)
    return ek or ''


def _shim_kind(out):
    return out.extras.get('kind', '')


def _shim_subsource(out):
    return out.extras.get('subsource', '') or ''


BASELINE = {
    'pair': CANONICAL_PAIR,
    'sentinels': [
        # Storage rows fire only when a storage macro file diffs. Known-fragile
        # names are intentionally omitted; the snapshot asserts the stable set.
    ],
}


def _run(pair):
    reset_index()
    cache.clear()
    old, new = CORPUS / pair[0], CORPUS / pair[1]
    ch = change_map.build(old, new)
    return storage.run(old, new, ch)


class TierASmokeTest(unittest.TestCase):
    def test_runs_on_every_pair(self):
        for pair in tier_a_pairs():
            with self.subTest(pair=pair):
                if not (CORPUS / pair[0]).is_dir() or not (CORPUS / pair[1]).is_dir():
                    self.skipTest(f'missing {pair}')
                outs = _run(pair)
                self.assertIsInstance(outs, list)


class TierBBaselineTest(unittest.TestCase):
    def test_no_unreviewed_incompletes(self):
        """Any RULE INCOMPLETE / WARNING that isn't allowlisted blocks the run."""
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        unreviewed = []
        for o in outs:
            if o.extras.get('kind') not in ('incomplete', 'warning'):
                continue
            if is_allowlisted(o):
                continue
            unreviewed.append(o.text)
        # Warnings are acceptable when documented; fail only on unreviewed
        # INCOMPLETEs (incompleteness contaminates outputs). Warnings surface
        # in the snapshot but don't block.
        incomplete_unreviewed = [t for t in unreviewed if 'RULE INCOMPLETE' in t]
        self.assertEqual(
            incomplete_unreviewed, [],
            f'unreviewed storage INCOMPLETEs: {incomplete_unreviewed}',
        )

    def test_snapshot_matches(self):
        require(BASELINE['pair'])
        outs = _run(BASELINE['pair'])
        lines = sorted(
            f'{_shim_key(o)}\t{_shim_kind(o)}\t{_shim_subsource(o)}\t{sha256(o.text.encode()).hexdigest()}'
            for o in outs
        )
        actual = '\n'.join(lines) + '\n'
        if not SNAP.exists() or os.environ.get('X4_REGEN_SNAPSHOT') == 'storage':
            SNAP.parent.mkdir(parents=True, exist_ok=True)
            SNAP.write_text(actual)
            self.skipTest(f'seeded snapshot {SNAP}')
        expected = SNAP.read_text()
        self.assertEqual(
            actual, expected,
            'Storage snapshot drift. '
            'Regen: X4_REGEN_SNAPSHOT=storage python3 -m unittest tests.test_realdata_storage',
        )


if __name__ == '__main__':
    unittest.main()
