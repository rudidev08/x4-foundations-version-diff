import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.entity_diff import diff_library, DiffReport
from src.lib import cache


FIX = Path(__file__).resolve().parent / 'fixtures' / '_diff_library_real'


class DiffLibraryTest(unittest.TestCase):
    def setUp(self):
        cache.clear()

    def test_report_shape(self):
        report = diff_library(
            FIX / 'v1', FIX / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        self.assertIsInstance(report, DiffReport)
        self.assertFalse(report.incomplete)

    def test_added_entity_tracks_sources(self):
        report = diff_library(
            FIX / 'v1', FIX / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        added_keys = [a.key for a in report.added]
        self.assertIn('new_dlc_ware', added_keys)
        rec = [a for a in report.added if a.key == 'new_dlc_ware'][0]
        self.assertIn('boron', rec.sources)

    def test_modified_entity_contributor_set(self):
        report = diff_library(
            FIX / 'v1', FIX / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        mod_keys = [m.key for m in report.modified]
        self.assertIn('changed_core_ware', mod_keys)

    def test_ref_sources_tracked(self):
        report = diff_library(
            FIX / 'v1', FIX / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        rec = [a for a in report.added if a.key == 'new_dlc_ware'][0]
        self.assertEqual(rec.ref_sources.get('component/@ref'), 'boron')

    def test_caches_by_resolved_path(self):
        r1 = diff_library(
            FIX / 'v1', FIX / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        r2 = diff_library(
            FIX / 'v1', FIX / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        self.assertIs(r1, r2)


class ConflictClassificationTest(unittest.TestCase):
    def setUp(self):
        cache.clear()

    def test_write_write_replace_same_attr_different_bodies_fails(self):
        report = diff_library(
            FIX / 'conflicts_ww' / 'v1', FIX / 'conflicts_ww' / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        self.assertTrue(report.incomplete)
        self.assertTrue(any('replace' in f[0] or 'write' in f[0] for f in report.failures))

    def test_positional_overlap_warns_not_fails(self):
        report = diff_library(
            FIX / 'conflicts_pos' / 'v1', FIX / 'conflicts_pos' / 'v2',
            file_rel='libraries/gamestarts.xml',
            entity_xpath='.//gamestart',
            key_fn_identity='default_id',
        )
        self.assertFalse(report.incomplete)
        self.assertTrue(report.warnings)

    def test_add_id_collision_fails(self):
        report = diff_library(
            FIX / 'conflicts_id' / 'v1', FIX / 'conflicts_id' / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        self.assertTrue(report.incomplete)

    def test_subtree_invalidation_fails(self):
        report = diff_library(
            FIX / 'conflicts_subtree' / 'v1', FIX / 'conflicts_subtree' / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        self.assertTrue(report.incomplete)

    def test_commutative_adds_no_warning(self):
        report = diff_library(
            FIX / 'conflicts_commutative' / 'v1', FIX / 'conflicts_commutative' / 'v2',
            file_rel='libraries/wares.xml',
            entity_xpath='.//ware',
            key_fn_identity='default_id',
        )
        self.assertFalse(report.incomplete)
        self.assertFalse(report.warnings)


class RawDetectionTest(unittest.TestCase):
    def setUp(self):
        cache.clear()

    def test_if_raw_dependency_fails(self):
        report = diff_library(
            FIX / 'conflicts_raw' / 'v1', FIX / 'conflicts_raw' / 'v2',
            file_rel='libraries/factions.xml',
            entity_xpath='.//faction',
            key_fn_identity='default_id',
        )
        self.assertTrue(report.incomplete)
        self.assertTrue(any('RAW' in f[0] or 'read-after-write' in f[0]
                            for f in report.failures))


if __name__ == '__main__':
    unittest.main()
