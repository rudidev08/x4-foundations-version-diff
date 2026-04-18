import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import change_map
from src.rules import shields


HERE = Path(__file__).resolve().parent


class ShieldsRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root1 = HERE / 'fixtures' / 'shields' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'shields' / 'TEST-2.00'
        cls.changes = change_map.build(cls.root1, cls.root2)
        cls.outputs = shields.run(cls.root1, cls.root2, cls.changes)

    def test_three_shield_outputs(self):
        self.assertEqual(
            len(self.outputs), 3,
            msg=f'expected 3 outputs, got: {[o.text for o in self.outputs]}',
        )

    def test_teladi_m_mk1_core(self):
        out = self._find('shield_tel_m_standard_01_mk1_macro')
        self.assertEqual(out.extras['source'], 'core')
        self.assertEqual(out.extras['type_old'], 'standard')
        self.assertEqual(out.extras['type_new'], 'advanced')
        self.assertIn('TEL M Shield Generator Mk1', out.text)
        self.assertIn('HP 5662→6500', out.text)
        self.assertIn('rate 25→72', out.text)
        self.assertIn('delay 0.57→14.3', out.text)
        self.assertIn('hull 500→800', out.text)
        self.assertIn('type standard→advanced', out.text)
        self.assertNotIn('[', out.text.split(']', 1)[1])  # no source-label bracket after [shields]

    def test_argon_s_mk1_core(self):
        out = self._find('shield_arg_s_standard_01_mk1_macro')
        self.assertEqual(out.extras['source'], 'core')
        self.assertEqual(out.extras['type_new'], 'advanced')
        self.assertIn('ARG S Shield Generator Mk1', out.text)
        self.assertIn('HP 827→1196', out.text)

    def test_argon_s_mk1_racer_timelines(self):
        out = self._find('shield_arg_s_racer_01_mk1_macro')
        self.assertEqual(out.extras['source'], 'timelines')
        self.assertEqual(out.extras['type_old'], 'small_racer')
        self.assertEqual(out.extras['type_new'], 'small_racer')
        self.assertIn('[timelines]', out.text)
        self.assertIn('(small_racer)', out.text)
        self.assertIn('HP 827→1196', out.text)
        self.assertIn('rate 100→50', out.text)
        self.assertIn('delay 8→15', out.text)
        self.assertNotIn('type ', out.text)  # type unchanged — should not appear

    def test_paranid_control_unchanged(self):
        par = [o for o in self.outputs if 'shield_par_s' in o.extras.get('macro', '')]
        self.assertEqual(par, [], msg='unchanged Paranid shield should emit no output')

    def _find(self, macro_name):
        matches = [o for o in self.outputs if o.extras.get('macro') == macro_name]
        self.assertEqual(len(matches), 1, msg=f'expected 1 match for {macro_name}, got {len(matches)}')
        return matches[0]


if __name__ == '__main__':
    unittest.main()
