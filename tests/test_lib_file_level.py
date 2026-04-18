import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.change_map import ChangeKind
from src.lib.file_level import diff_files, render_modified


FIX = Path(__file__).resolve().parent / 'fixtures' / '_file_level'


class FileLevelTest(unittest.TestCase):
    def test_returns_added_modified_removed(self):
        results = diff_files(FIX / 'v1', FIX / 'v2', globs=['md/*.xml'])
        kinds = {r[1] for r in results}
        self.assertEqual(kinds, {ChangeKind.ADDED, ChangeKind.MODIFIED, ChangeKind.DELETED})

    def test_returns_bytes(self):
        results = diff_files(FIX / 'v1', FIX / 'v2', globs=['md/*.xml'])
        mod = [r for r in results if r[1] == ChangeKind.MODIFIED][0]
        self.assertIsInstance(mod[2], (bytes, type(None)))
        self.assertIsInstance(mod[3], (bytes, type(None)))

    def test_render_modified_small_diff(self):
        old = b'<root>\n  <a/>\n</root>\n'
        new = b'<root>\n  <a/>\n  <b/>\n</root>\n'
        text, extras = render_modified('md/x.xml', old, new, tag='quests', name='x')
        self.assertIn('+1/-0', text)
        self.assertIn('<b/>', extras['diff'])
        self.assertFalse(extras.get('diff_truncated'))

    def test_render_modified_truncates(self):
        lines_old = ['a'] * 6000
        lines_new = ['b'] * 6000
        old = ('\n'.join(lines_old)).encode()
        new = ('\n'.join(lines_new)).encode()
        text, extras = render_modified('md/big.xml', old, new, tag='quests', name='big')
        self.assertTrue(extras['diff_truncated'])
        self.assertIn('truncated', extras['diff'])


if __name__ == '__main__':
    unittest.main()
