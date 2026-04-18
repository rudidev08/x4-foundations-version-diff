# tests/test_lib_cache.py
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache as C


class CacheTest(unittest.TestCase):
    def setUp(self):
        C.clear()

    def test_miss_then_hit(self):
        calls = []
        def produce():
            calls.append(1)
            return 'value'
        key = ('a', 'b', 'c', 'd', 'id0', True)
        v1 = C.get_or_compute(key, produce)
        v2 = C.get_or_compute(key, produce)
        self.assertEqual(v1, v2)
        self.assertEqual(len(calls), 1)

    def test_different_keys_miss(self):
        calls = []
        def produce():
            calls.append(1)
            return object()
        C.get_or_compute(('a',), produce)
        C.get_or_compute(('b',), produce)
        self.assertEqual(len(calls), 2)

    def test_clear(self):
        calls = []
        def produce():
            calls.append(1)
            return 1
        C.get_or_compute(('k',), produce)
        C.clear()
        C.get_or_compute(('k',), produce)
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
