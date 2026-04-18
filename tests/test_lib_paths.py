import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.paths import source_of, resolve_macro_path, reset_index


FIX = Path(__file__).resolve().parent / 'fixtures' / '_paths'


class SourceOfTest(unittest.TestCase):
    def test_core(self):
        self.assertEqual(source_of('assets/props/Engines/macros/x.xml'), 'core')

    def test_timelines(self):
        self.assertEqual(source_of('extensions/ego_dlc_timelines/assets/x.xml'), 'timelines')

    def test_unknown_extension(self):
        self.assertEqual(source_of('extensions/mycustomextension/x.xml'), 'mycustomextension')


class ResolveMacroPathTest(unittest.TestCase):
    def setUp(self):
        reset_index()

    def test_engines_core(self):
        p = resolve_macro_path(FIX / 'root', FIX / 'root', 'engine_arg_m_allround_01_mk1_macro', 'engines')
        self.assertIsNotNone(p)
        self.assertTrue(str(p).endswith('engine_arg_m_allround_01_mk1_macro.xml'))

    def test_weapons_case_insensitive(self):
        p = resolve_macro_path(FIX / 'root', FIX / 'root', 'weapon_arg_m_beam_01_mk1_macro', 'weapons')
        self.assertIsNotNone(p)

    def test_bullet_case_insensitive_core_vs_dlc(self):
        # Core uses 'weaponFx' capitalization; DLCs use 'weaponfx'.
        p = resolve_macro_path(FIX / 'root', FIX / 'root', 'bullet_gen_m_dumbfire_01_mk1_macro', 'bullet')
        self.assertIsNotNone(p)

    def test_pkg_root_preferred_over_core(self):
        pkg = FIX / 'root' / 'extensions' / 'ego_dlc_boron'
        p = resolve_macro_path(FIX / 'root', pkg, 'engine_overridden_macro', 'engines')
        self.assertTrue('ego_dlc_boron' in str(p))


if __name__ == '__main__':
    unittest.main()
