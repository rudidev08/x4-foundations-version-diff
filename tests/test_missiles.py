import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rules import missiles


HERE = Path(__file__).resolve().parent


class MissilesRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root1 = HERE / 'fixtures' / 'missiles' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'missiles' / 'TEST-2.00'
        cls.outputs = missiles.run(cls.root1, cls.root2)

    def test_two_missile_outputs(self):
        # torpedo (deprecation + stat diff), new gen_m_guided (added). Cluster unchanged.
        self.assertEqual(
            len(self.outputs), 2,
            msg=f'expected 2 outputs, got: {[o.text for o in self.outputs]}',
        )

    def test_torpedo_deprecated_plus_shielddisruption(self):
        out = self._find('missile_torpedo_heavy_mk1')
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertTrue(out.extras['newly_deprecated'])
        self.assertIn('DEPRECATED', out.text)
        self.assertIn('Heavy Torpedo Missile Mk1', out.text)
        self.assertIn('(torpedo)', out.text)
        self.assertIn('shielddisruption None→10', out.text)

    def test_new_medium_guided(self):
        out = self._find('missile_gen_m_guided_01_mk1')
        self.assertEqual(out.extras['kind'], 'added')
        self.assertEqual(out.extras['class'], 'mediumguided')
        self.assertIn('NEW', out.text)
        self.assertIn('Medium Guided Missile', out.text)
        self.assertIn('(mediumguided)', out.text)
        self.assertIn('damage 1100', out.text)
        self.assertIn('range 14000', out.text)
        self.assertIn('guided', out.text)

    def test_cluster_unchanged(self):
        cluster = [o for o in self.outputs if o.extras.get('ware_id') == 'missile_cluster_light_mk1']
        self.assertEqual(cluster, [], msg='unchanged cluster should emit no output')

    def _find(self, ware_id: str):
        matches = [o for o in self.outputs if o.extras.get('ware_id') == ware_id]
        self.assertEqual(len(matches), 1, msg=f'expected 1 match for {ware_id}, got {len(matches)}')
        return matches[0]


if __name__ == '__main__':
    unittest.main()
