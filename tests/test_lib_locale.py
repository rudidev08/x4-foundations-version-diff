# tests/test_lib_locale.py
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.locale import Locale, resolve_attr_ref, display_name


FIX = Path(__file__).resolve().parent / 'fixtures' / '_locale'


class LocaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = FIX / 'root'

    def test_core_entry_resolves(self):
        loc = Locale.build(self.root)
        self.assertEqual(loc.get(99001, 1), 'Argon Engine')

    def test_dlc_only_entry_resolves(self):
        loc = Locale.build(self.root)
        self.assertEqual(loc.get(99001, 100), 'Boron Coil')

    def test_dlc_overrides_core_and_records_collision(self):
        loc = Locale.build(self.root)
        self.assertEqual(loc.get(99001, 2), 'BORON REWRITE')  # boron overrides alphabetically-later
        # Collisions recorded in warning shape
        self.assertTrue(loc.collisions)
        text, extras = loc.collisions[0]
        self.assertIn('locale collision', text)
        self.assertEqual(extras['page'], 99001)
        self.assertEqual(extras['id'], 2)
        self.assertEqual(extras['core_text'], 'Core Text')
        self.assertEqual(extras['dlc_text'], 'BORON REWRITE')
        self.assertEqual(extras['dlc_name'], 'boron')

    def test_resolve_attr_ref_ware_name(self):
        loc = Locale.build(self.root)
        elem = ET.fromstring('<ware name="{99001,1}"/>')
        self.assertEqual(resolve_attr_ref(elem, loc, attr='name'), 'Argon Engine')

    def test_resolve_attr_ref_fallback_to_raw(self):
        loc = Locale.build(self.root)
        elem = ET.fromstring('<x name="not-a-ref"/>')
        self.assertEqual(resolve_attr_ref(elem, loc, attr='name'), 'not-a-ref')

    def test_resolve_attr_ref_fallback_override(self):
        loc = Locale.build(self.root)
        elem = ET.fromstring('<x name="{99001,9999}"/>')  # unresolved
        self.assertEqual(
            resolve_attr_ref(elem, loc, attr='name', fallback='missing'),
            'missing',
        )

    def test_display_name_still_works(self):
        loc = Locale.build(self.root)
        macro = ET.fromstring(
            '<macro name="m1"><properties><identification name="{99001,1}"/></properties></macro>'
        )
        self.assertEqual(display_name(macro, loc), 'Argon Engine')

    def test_positional_path_constructor_back_compat(self):
        """Locale(path) is the back-compat constructor that shields/missiles
        use. Pin it with a direct test so future refactors can't silently break it.
        """
        loc = Locale(self.root / 't' / '0001-l044.xml')
        self.assertEqual(loc.get(99001, 1), 'Argon Engine')
        self.assertEqual(loc.collisions, [])


if __name__ == '__main__':
    unittest.main()
