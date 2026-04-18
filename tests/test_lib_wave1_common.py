import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rules._wave1_common import ware_owner, owns, diff_productions


def _w(**attrs):
    el = ET.Element('ware', attrib={k: str(v) for k, v in attrs.items()})
    return el


class OwnershipTest(unittest.TestCase):
    def test_engines_ware_owned_by_engines(self):
        w = _w(id='engine_arg_m_allround_01_mk1', group='engines')
        self.assertEqual(ware_owner(w), 'engines')

    def test_spacesuit_engine_owned_by_equipment(self):
        w = _w(id='engine_gen_spacesuit_01_mk1', group='engines')
        self.assertEqual(ware_owner(w), 'equipment')

    def test_spacesuit_weapon_owned_by_equipment(self):
        w = _w(id='weapon_gen_spacesuit_laser_01_mk1', group='weapons')
        self.assertEqual(ware_owner(w), 'equipment')

    def test_satellite_owned_by_equipment(self):
        w = _w(id='satellite_mk1', group='hardware')
        self.assertEqual(ware_owner(w), 'equipment')

    def test_shield_excluded(self):
        w = _w(id='shield_arg_m_standard_01_mk1', group='shields')
        self.assertIsNone(ware_owner(w))

    def test_shield_with_personalupgrade_still_excluded(self):
        w = _w(id='shield_arg_m_standard_01_mk1', group='shields', tags='personalupgrade')
        self.assertIsNone(ware_owner(w))

    def test_groupless_ware_falls_to_wares(self):
        w = _w(id='zz_test_groupless_ware')
        self.assertEqual(ware_owner(w), 'wares')

    def test_drone_excluded(self):
        w = _w(id='ship_gen_xs_cargodrone_01', group='drones')
        self.assertIsNone(ware_owner(w))

    def test_owns_helper(self):
        w = _w(id='engine_arg_m_allround_01_mk1', group='engines')
        self.assertTrue(owns(w, 'engines'))
        self.assertFalse(owns(w, 'equipment'))


class DiffProductionsTest(unittest.TestCase):
    def _make(self, xml):
        return ET.fromstring(xml)

    def test_method_added(self):
        old = self._make('<ware/>')
        new = self._make('<ware><production method="default" time="10" amount="1"/></ware>')
        self.assertEqual(diff_productions(old, new), ['production[method=default] added'])

    def test_method_removed(self):
        old = self._make('<ware><production method="default" time="10" amount="1"/></ware>')
        new = self._make('<ware/>')
        self.assertEqual(diff_productions(old, new), ['production[method=default] removed'])

    def test_time_change(self):
        old = self._make('<ware><production method="default" time="10" amount="1"/></ware>')
        new = self._make('<ware><production method="default" time="20" amount="1"/></ware>')
        self.assertEqual(diff_productions(old, new),
                         ['production[method=default] time 10→20'])

    def test_amount_change(self):
        old = self._make('<ware><production method="default" time="10" amount="1"/></ware>')
        new = self._make('<ware><production method="default" time="10" amount="5"/></ware>')
        self.assertEqual(diff_productions(old, new),
                         ['production[method=default] amount 1→5'])

    def test_primary_ware_amount_change(self):
        old = self._make(
            '<ware><production method="default" time="10" amount="1">'
            '<primary><ware ware="silicon" amount="2"/></primary>'
            '</production></ware>'
        )
        new = self._make(
            '<ware><production method="default" time="10" amount="1">'
            '<primary><ware ware="silicon" amount="4"/></primary>'
            '</production></ware>'
        )
        self.assertEqual(diff_productions(old, new),
                         ['production[method=default] primary.silicon 2→4'])

    def test_primary_ware_added_and_removed(self):
        old = self._make(
            '<ware><production method="default" time="10" amount="1">'
            '<primary><ware ware="silicon" amount="2"/></primary>'
            '</production></ware>'
        )
        new = self._make(
            '<ware><production method="default" time="10" amount="1">'
            '<primary><ware ware="iron" amount="3"/></primary>'
            '</production></ware>'
        )
        self.assertEqual(sorted(diff_productions(old, new)), [
            'production[method=default] primary.iron added',
            'production[method=default] primary.silicon removed',
        ])


if __name__ == '__main__':
    unittest.main()
