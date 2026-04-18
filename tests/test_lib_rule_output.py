# tests/test_lib_rule_output.py
import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.rule_output import (
    RuleOutput, render_sources, snapshot_line, diagnostic_entity_key,
)


class RuleOutputTest(unittest.TestCase):
    def test_dataclass_fields(self):
        r = RuleOutput(tag='engines', text='x', extras={'entity_key': 'e1', 'kind': 'added'})
        self.assertEqual(r.tag, 'engines')
        self.assertEqual(r.extras['entity_key'], 'e1')

    def test_render_sources_core_only(self):
        self.assertEqual(render_sources(['core'], ['core']), '[core]')

    def test_render_sources_equal_sets(self):
        self.assertEqual(render_sources(['core', 'boron'], ['core', 'boron']), '[boron+core]')

    def test_render_sources_different_sets(self):
        self.assertEqual(
            render_sources(['core'], ['core', 'boron']),
            '[core→boron+core]',
        )

    def test_render_sources_added_only(self):
        self.assertEqual(render_sources(None, ['core', 'timelines']), '[core+timelines]')

    def test_snapshot_line_stable(self):
        r = RuleOutput(tag='x', text='hello', extras={
            'entity_key': ('module', 'prod_bor_medicalsupplies'),
            'kind': 'modified',
            'subsource': 'module',
        })
        line = snapshot_line(r)
        h = hashlib.sha256(b'hello').hexdigest()
        self.assertIn(h, line)
        self.assertIn('module', line)
        self.assertIn('modified', line)

    def test_diagnostic_entity_key_stable(self):
        k1 = diagnostic_entity_key('engines', 'some diagnostic text')
        k2 = diagnostic_entity_key('engines', 'some diagnostic text')
        self.assertEqual(k1, k2)
        self.assertEqual(k1[0], 'diagnostic')
        self.assertEqual(k1[1], 'engines')


if __name__ == '__main__':
    unittest.main()
