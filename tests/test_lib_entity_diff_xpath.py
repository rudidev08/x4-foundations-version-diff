import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.entity_diff import xpath_find, XPathError, AttrRef


TREE = ET.fromstring('''
<root>
  <job id="a" friendgroup="x">
    <location cluster="c1">
      <factions>f1</factions>
    </location>
  </job>
  <job id="b" friendgroup="y">
    <location cluster="c2"/>
  </job>
  <ware id="X">
    <price min="10" max="20"/>
  </ware>
</root>
''')


class XPathTest(unittest.TestCase):
    def test_simple_abs(self):
        m = xpath_find(TREE, '/root/job')
        self.assertEqual(len(m), 2)

    def test_descendant(self):
        m = xpath_find(TREE, '//job')
        self.assertEqual(len(m), 2)

    def test_attr_predicate(self):
        m = xpath_find(TREE, "//job[@id='a']")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].get('id'), 'a')

    def test_chained_predicates(self):
        m = xpath_find(TREE, "//job[@id='a'][@friendgroup='x']")
        self.assertEqual(len(m), 1)

    def test_absent_child_predicate(self):
        m = xpath_find(TREE, "//job/location[not(factions)]")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].get('cluster'), 'c2')

    def test_attr_terminator(self):
        m = xpath_find(TREE, "//ware[@id='X']/@max")
        self.assertEqual(len(m), 0)  # @max is on price, not ware
        m = xpath_find(TREE, "//ware[@id='X']/price/@max")
        self.assertEqual(len(m), 1)
        self.assertIsInstance(m[0], AttrRef)
        self.assertEqual(m[0].name, 'max')
        self.assertEqual(m[0].value, '20')

    def test_not_function(self):
        m = xpath_find(TREE, "//job[not(location/factions)]")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].get('id'), 'b')

    def test_not_with_descendant_axis(self):
        # //factions anchors to document root, not candidate subtree.
        # First job has <factions>; second does not. So [not(//factions)] would be
        # false for BOTH jobs if anchored correctly (factions exists somewhere in
        # the doc). Result: 0 matches — not an empty subset, but 0 — confirming
        # the // anchor is document-wide.
        m = xpath_find(TREE, "//job[not(//factions)]")
        self.assertEqual(len(m), 0)

    def test_positional_literal(self):
        # Real usage: append_to_list[@name='X'][1] — 1-indexed, picks Nth match.
        m = xpath_find(TREE, "//job[1]")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].get('id'), 'a')
        m = xpath_find(TREE, "//job[2]")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].get('id'), 'b')

    def test_unsupported_raises(self):
        with self.assertRaises(XPathError):
            xpath_find(TREE, "//job[position()=1]")
        with self.assertRaises(XPathError):
            xpath_find(TREE, "//job[last()]")


if __name__ == '__main__':
    unittest.main()
