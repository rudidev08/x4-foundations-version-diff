"""Real-data test utilities: version detection, loud skip messages, Tier A/B
scaffolding. Imported by every tests/test_realdata_<rule>.py.
"""
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'x4-data'
CANONICAL_PAIR = ('8.00H4', '9.00B6')


def versions_present(pair: tuple[str, str]) -> bool:
    return (CORPUS / pair[0]).is_dir() and (CORPUS / pair[1]).is_dir()


def skip_reason(pair):
    return (f'x4-data/{pair[0]}/ or x4-data/{pair[1]}/ not present — '
            f'extract these versions locally to enable this test.')


def require(pair):
    if not versions_present(pair):
        raise unittest.SkipTest(skip_reason(pair))


def consecutive_9_pairs():
    return [
        ('9.00B1', '9.00B2'), ('9.00B2', '9.00B3'),
        ('9.00B3', '9.00B4'), ('9.00B4', '9.00B5'),
        ('9.00B5', '9.00B6'),
    ]


def tier_a_pairs():
    pairs = [CANONICAL_PAIR]
    if os.environ.get('X4_REALDATA_FULL'):
        pairs += consecutive_9_pairs()
    return pairs
