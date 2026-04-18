import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib import cache
from src.lib.entity_diff import diff_library
from src.lib.rule_output import RuleOutput
from src.lib.check_incomplete import forward_incomplete


FIX = Path(__file__).resolve().parent / 'fixtures' / '_diff_library_real'


class ContaminationTest(unittest.TestCase):
    def setUp(self):
        cache.clear()

    def test_affected_keys_propagate_to_outputs(self):
        report = diff_library(
            FIX / 'conflicts_ww' / 'v1', FIX / 'conflicts_ww' / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        self.assertTrue(report.incomplete)
        outs = [RuleOutput('wares', '[wares] X: price 10->80', extras={
            'entity_key': 'X', 'kind': 'modified', 'subsource': None,
        })]
        forward_incomplete(report, outs, tag='wares')
        self.assertTrue(outs[0].extras.get('incomplete'))
        self.assertEqual(outs[-1].extras['kind'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
