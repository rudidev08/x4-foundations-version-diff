# tests/test_lib_macro_diff.py
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.macro_diff import diff_attrs, collect_attrs


FIELDS = [
    ('properties/hull',    'max',         'HP'),
    ('properties/recharge', 'rate',       'rate'),
    ('properties/recharge', 'delay',      'delay'),
]


class MacroDiffTest(unittest.TestCase):
    def test_changed_attr(self):
        old = ET.fromstring(
            '<macro><properties><hull max="100"/><recharge rate="5" delay="1"/></properties></macro>'
        )
        new = ET.fromstring(
            '<macro><properties><hull max="120"/><recharge rate="5" delay="1"/></properties></macro>'
        )
        diff = diff_attrs(old, new, FIELDS)
        self.assertEqual(diff, {'HP': ('100', '120')})

    def test_new_attr_appears(self):
        old = ET.fromstring('<macro><properties/></macro>')
        new = ET.fromstring('<macro><properties><hull max="100"/></properties></macro>')
        self.assertEqual(diff_attrs(old, new, FIELDS), {'HP': (None, '100')})

    def test_attr_removed(self):
        old = ET.fromstring('<macro><properties><hull max="100"/></properties></macro>')
        new = ET.fromstring('<macro><properties/></macro>')
        self.assertEqual(diff_attrs(old, new, FIELDS), {'HP': ('100', None)})

    def test_no_change(self):
        old = ET.fromstring('<macro><properties><hull max="100"/></properties></macro>')
        new = ET.fromstring('<macro><properties><hull max="100"/></properties></macro>')
        self.assertEqual(diff_attrs(old, new, FIELDS), {})

    def test_collect_attrs_snapshot(self):
        macro = ET.fromstring(
            '<macro><properties><hull max="100"/><recharge rate="5" delay="1"/></properties></macro>'
        )
        self.assertEqual(collect_attrs(macro, FIELDS),
                         {'HP': '100', 'rate': '5', 'delay': '1'})


if __name__ == '__main__':
    unittest.main()
