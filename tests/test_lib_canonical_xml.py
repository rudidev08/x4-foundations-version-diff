"""Tests for `src.lib.canonical_xml.canonical_bytes` deterministic serializer."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.canonical_xml import canonical_bytes


class CanonicalBytesTest(unittest.TestCase):
    def test_same_input_same_output_across_reruns(self):
        """Reruns over the same input produce byte-identical output."""
        src = '<root a="1" b="2"><child x="y"/></root>'
        elem = ET.fromstring(src)
        b1 = canonical_bytes(elem)
        b2 = canonical_bytes(elem)
        self.assertEqual(b1, b2)

    def test_attribute_order_normalized(self):
        """Differing input attribute order yields the same canonical bytes."""
        e1 = ET.fromstring('<root a="1" b="2" c="3"/>')
        e2 = ET.fromstring('<root c="3" a="1" b="2"/>')
        e3 = ET.fromstring('<root b="2" c="3" a="1"/>')
        out1 = canonical_bytes(e1)
        out2 = canonical_bytes(e2)
        out3 = canonical_bytes(e3)
        self.assertEqual(out1, out2)
        self.assertEqual(out2, out3)
        # Alphabetical order is present in the bytes.
        self.assertIn(b'a="1" b="2" c="3"', out1)

    def test_whitespace_indented(self):
        """Nested elements get indented with two-space step via ET.indent."""
        src = '<root><a><b>text</b></a></root>'
        elem = ET.fromstring(src)
        out = canonical_bytes(elem).decode('utf-8')
        # Must contain newline + two-space indent before child tags.
        self.assertIn('\n  <a>', out)
        self.assertIn('\n    <b>', out)

    def test_xml_declaration_prefix(self):
        """Output always starts with the fixed XML declaration."""
        elem = ET.fromstring('<x/>')
        out = canonical_bytes(elem)
        self.assertTrue(out.startswith(b'<?xml version="1.0" encoding="utf-8"?>\n'))

    def test_input_tree_not_mutated(self):
        """Canonicalization runs on a deep copy; input tree attr order is safe."""
        elem = ET.fromstring('<root c="3" a="1" b="2"/>')
        original_attrib = list(elem.attrib.keys())
        _ = canonical_bytes(elem)
        # The order of keys in the source elem.attrib dict is implementation
        # dependent, but the point is it must not be REORDERED by our helper.
        self.assertEqual(list(elem.attrib.keys()), original_attrib)

    def test_deep_tree(self):
        """Deeply nested trees serialize deterministically."""
        src = (
            '<root>'
            '<a z="1" b="2">'
            '<b x="3" y="4"><c k="5"/></b>'
            '</a>'
            '</root>'
        )
        elem = ET.fromstring(src)
        out = canonical_bytes(elem)
        # Second run must match.
        self.assertEqual(canonical_bytes(elem), out)
        # Inner attr ordering is alphabetical.
        self.assertIn(b'b="2" z="1"', out)
        self.assertIn(b'x="3" y="4"', out)


if __name__ == '__main__':
    unittest.main()
