"""Unit tests for the `sectors` rule.

Fixture design: two entities per internal label (16 total), covering the
Wave 2 case matrix cumulatively — added / removed / modified across every
internal label, with per-connection sub-entity rows for the map/highway
groups, plus dedicated cases for incomplete propagation, DLC-sourced
provenance, and warnings (positional overlap).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import change_map
from src.lib import cache
from src.rules import sectors


HERE = Path(__file__).resolve().parent


class _BaseSectorsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cache.clear()
        cls.root1 = HERE / 'fixtures' / 'sectors' / 'TEST-1.00'
        cls.root2 = HERE / 'fixtures' / 'sectors' / 'TEST-2.00'
        cls.changes = change_map.build(cls.root1, cls.root2)
        cls.outputs = sectors.run(cls.root1, cls.root2, changes=cls.changes)

    def _find(self, entity_key):
        return [o for o in self.outputs
                if o.extras.get('entity_key') == entity_key]


class GalaxyTest(_BaseSectorsTest):
    def test_added_connection(self):
        matches = self._find(('galaxy', 'Cluster_03_connection'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'added')
        self.assertIn('NEW', out.text)
        self.assertIn('Cluster_03_macro', out.text)
        self.assertEqual(out.extras['subsource'], 'galaxy')
        self.assertEqual(out.extras['classifications'], ['galaxy'])

    def test_modified_connection_offset(self):
        matches = self._find(('galaxy', 'Cluster_02_connection'))
        self.assertEqual(len(matches), 1)
        out = matches[0]
        self.assertEqual(out.extras['kind'], 'modified')
        self.assertIn('offset.position', out.text)
        self.assertIn('25980000', out.text)
        self.assertIn('26000000', out.text)

    def test_unchanged_cluster_01_no_output(self):
        # No offset and same macro.ref → unchanged, no output.
        matches = self._find(('galaxy', 'Cluster_01_connection'))
        self.assertEqual(matches, [])


class MapClustersTest(_BaseSectorsTest):
    def test_added_parent_emits_parent_and_child_row(self):
        parent = self._find(('map_clusters', 'Cluster_03_macro'))
        self.assertEqual(len(parent), 1)
        self.assertEqual(parent[0].extras['kind'], 'added')
        self.assertIn('NEW', parent[0].text)
        self.assertIn('1 connections', parent[0].text)
        self.assertEqual(parent[0].extras['subsource'], 'map_clusters')
        # Sub-source label carries the INTERNAL token, not the user-facing one.
        # The user-facing 'map' appears in classifications.
        self.assertIn('map', parent[0].extras['classifications'])

        child = self._find(('map_clusters', 'Cluster_03_macro',
                            'Cluster_03_Sector001_connection'))
        self.assertEqual(len(child), 1)
        self.assertEqual(child[0].extras['kind'], 'added')
        self.assertIn('connection', child[0].extras['classifications'])

    def test_parent_with_child_modification_emits_only_child_row(self):
        # Cluster_01_macro itself unchanged (same attrs); only its children diff.
        parent = self._find(('map_clusters', 'Cluster_01_macro'))
        self.assertEqual(parent, [])
        child_mod = self._find(('map_clusters', 'Cluster_01_macro',
                                'Cluster_01_Sector002_connection'))
        self.assertEqual(len(child_mod), 1)
        self.assertIn('offset.position', child_mod[0].text)

        child_add = self._find(('map_clusters', 'Cluster_01_macro',
                                'Cluster_01_Sector003_connection'))
        self.assertEqual(len(child_add), 1)
        self.assertEqual(child_add[0].extras['kind'], 'added')


class MapSectorsTest(_BaseSectorsTest):
    def test_added_parent(self):
        matches = self._find(('map_sectors', 'Cluster_03_Sector001_macro'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'added')
        self.assertEqual(matches[0].extras['subsource'], 'map_sectors')

    def test_removed_parent_with_child(self):
        # Cluster_01_Sector002_macro removed in TEST-2.00.
        matches = self._find(('map_sectors', 'Cluster_01_Sector002_macro'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')
        child = self._find(('map_sectors', 'Cluster_01_Sector002_macro',
                            'Zone002_connection'))
        self.assertEqual(len(child), 1)
        self.assertEqual(child[0].extras['kind'], 'removed')


class MapZonesTest(_BaseSectorsTest):
    def test_modified_child_connection_target(self):
        # Cluster_01_Sector001_Zone001_macro — same parent attrs, child changes.
        parent = self._find(('map_zones',
                             'Cluster_01_Sector001_Zone001_macro'))
        self.assertEqual(parent, [])
        child = self._find(('map_zones',
                            'Cluster_01_Sector001_Zone001_macro',
                            'AsteroidBelt_01_connection'))
        self.assertEqual(len(child), 1)
        self.assertEqual(child[0].extras['kind'], 'modified')
        self.assertIn('AsteroidBelt_macro', child[0].text)
        self.assertIn('AsteroidBelt_large_macro', child[0].text)

    def test_removed_zone(self):
        matches = self._find(('map_zones',
                              'Cluster_01_Sector002_Zone001_macro'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')


class HighwaySecTest(_BaseSectorsTest):
    def test_added_highway(self):
        matches = self._find(('highway_sec',
                              'SuperHighway003_Cluster_03_macro'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'added')
        self.assertEqual(matches[0].extras['subsource'], 'highway_sec')
        # User-facing 'highway' appears in classifications.
        self.assertIn('highway', matches[0].extras['classifications'])

    def test_removed_highway(self):
        matches = self._find(('highway_sec',
                              'SuperHighway002_Cluster_01_macro'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')


class HighwayZoneTest(_BaseSectorsTest):
    def test_modified_child_connection(self):
        child = self._find(('highway_zone', 'LocalHighway001_Cluster_01_macro',
                            'entrypoint'))
        self.assertEqual(len(child), 1)
        self.assertEqual(child[0].extras['kind'], 'modified')
        self.assertIn('ZoneGate_A_macro', child[0].text)

    def test_added_highway(self):
        matches = self._find(('highway_zone',
                              'LocalHighway003_Cluster_03_macro'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'added')
        self.assertEqual(matches[0].extras['subsource'], 'highway_zone')


class RegionYieldTest(_BaseSectorsTest):
    def test_added(self):
        matches = self._find(('regionyield', 'sphere_small_helium_low'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'added')
        self.assertIn('ware=helium', matches[0].text)

    def test_modified_yield_and_rating(self):
        matches = self._find(('regionyield', 'sphere_small_ore_low'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'modified')
        self.assertIn('yield 15000→20000', matches[0].text)
        self.assertIn('rating 2→3', matches[0].text)

    def test_removed(self):
        matches = self._find(('regionyield', 'sphere_small_silicon_low'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'removed')


class RegionDefTest(_BaseSectorsTest):
    def test_added(self):
        matches = self._find(('regiondef', 'region_cluster_03_asteroids_1'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'added')

    def test_modified_density_boundary_fields(self):
        matches = self._find(('regiondef', 'region_cluster_01_asteroids_1'))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].extras['kind'], 'modified')
        self.assertIn('density 0.10→0.20', matches[0].text)
        self.assertIn('boundary.size', matches[0].text)
        self.assertIn('fields', matches[0].text)


class ClassificationSubsourceTest(_BaseSectorsTest):
    """Internal subsource stays on extras.subsource; user-facing token lives
    in classifications — keeps Tier B snapshots distinct per file while the
    text channel groups the map/highway family together for humans."""

    def test_map_family_shares_user_facing_token(self):
        subsources = {o.extras['subsource'] for o in self.outputs
                      if o.extras.get('subsource', '').startswith('map_')}
        self.assertEqual(subsources,
                         {'map_clusters', 'map_sectors', 'map_zones'})
        classifications = [o.extras['classifications'] for o in self.outputs
                           if o.extras.get('subsource', '').startswith('map_')]
        # Every map-family row shares the 'map' user-facing token.
        self.assertTrue(
            all('map' in cls for cls in classifications),
            f'classifications: {classifications}',
        )

    def test_highway_family_shares_user_facing_token(self):
        subsources = {o.extras['subsource'] for o in self.outputs
                      if o.extras.get('subsource', '').startswith('highway_')}
        self.assertEqual(subsources, {'highway_sec', 'highway_zone'})


# --- Incomplete / warning scenarios -------------------------------------


class SectorsIncompleteTest(unittest.TestCase):
    """Bad-XPath DLC patch produces an incomplete sentinel scoped to the
    specific internal label that owns the failure."""

    def test_incomplete_sentinel_scoped_to_internal_label(self):
        cache.clear()
        root1 = HERE / 'fixtures' / 'sectors' / 'TEST-1.00'
        root2 = HERE / 'fixtures' / 'sectors' / 'TEST-2.00-incomplete'
        outputs = sectors.run(root1, root2)
        incompletes = [o for o in outputs if o.extras.get('kind') == 'incomplete']
        self.assertTrue(incompletes,
                        f'no incomplete sentinel in: {[o.text for o in outputs]}')
        # Sentinel should carry an internal label, not a user-facing token.
        subsources = {o.extras.get('subsource') for o in incompletes}
        self.assertTrue(
            subsources & {'map_clusters', 'map_sectors', 'map_zones',
                          'highway_sec', 'highway_zone',
                          'galaxy', 'regionyield', 'regiondef'},
            f'unexpected incomplete subsources: {subsources}',
        )


class SectorsContaminationScopingTest(unittest.TestCase):
    """A failure in one map-family file must not contaminate outputs in
    sibling map-family files — the whole point of distinct internal labels."""

    def test_sibling_map_files_stay_complete(self):
        cache.clear()
        root1 = HERE / 'fixtures' / 'sectors' / 'TEST-1.00'
        root2 = HERE / 'fixtures' / 'sectors' / 'TEST-2.00-incomplete'
        outputs = sectors.run(root1, root2)
        # map_sectors and map_zones must NOT be marked incomplete because
        # the induced failure targets clusters.xml only.
        contaminated_siblings = [
            o for o in outputs
            if o.extras.get('incomplete')
            and o.extras.get('subsource') in ('map_sectors', 'map_zones')
        ]
        self.assertEqual(
            contaminated_siblings, [],
            f'sibling files were contaminated: '
            f'{[(o.extras.get("subsource"), o.text) for o in contaminated_siblings]}',
        )


class SectorsWarningTest(unittest.TestCase):
    """Positional overlap from two DLCs adding after the same macro → warning."""

    def test_positional_overlap_surfaces_as_warning(self):
        cache.clear()
        root1 = HERE / 'fixtures' / 'sectors' / 'TEST-1.00'
        root2 = HERE / 'fixtures' / 'sectors' / 'TEST-2.00-warning'
        outputs = sectors.run(root1, root2)
        warnings = [o for o in outputs if o.extras.get('kind') == 'warning']
        self.assertTrue(warnings,
                        f'no warning in: {[o.text for o in outputs]}')
        self.assertTrue(any('positional overlap' in w.text for w in warnings),
                        f'no positional-overlap warning: '
                        f'{[w.text for w in warnings]}')


class SectorsPerConnectionContaminationTest(unittest.TestCase):
    """When a parent macro's patch fails, every per-connection child row
    emitted under that parent must be marked incomplete — otherwise silent
    changes slip through as rows still advertised as clean."""

    def test_child_rows_of_broken_parent_are_marked_incomplete(self):
        cache.clear()
        root1 = HERE / 'fixtures' / 'sectors' / 'TEST-1.00'
        root2 = HERE / 'fixtures' / 'sectors' / 'TEST-2.00-contamination'
        outputs = sectors.run(root1, root2)
        # Induced failure's sel names Cluster_01_macro. The rule emitted
        # child rows for Cluster_01_Sector002_connection (modified) and
        # Cluster_01_Sector003_connection (added). Both should be marked
        # incomplete after per-connection expansion.
        expected_contaminated = {
            ('map_clusters', 'Cluster_01_macro',
             'Cluster_01_Sector002_connection'),
            ('map_clusters', 'Cluster_01_macro',
             'Cluster_01_Sector003_connection'),
        }
        actual_contaminated = {
            o.extras.get('entity_key') for o in outputs
            if o.extras.get('incomplete')
            and o.extras.get('kind') != 'incomplete'
        }
        self.assertTrue(
            expected_contaminated.issubset(actual_contaminated),
            f'expected {expected_contaminated} contaminated, got '
            f'{actual_contaminated}',
        )

    def test_sibling_file_children_not_contaminated(self):
        cache.clear()
        root1 = HERE / 'fixtures' / 'sectors' / 'TEST-1.00'
        root2 = HERE / 'fixtures' / 'sectors' / 'TEST-2.00-contamination'
        outputs = sectors.run(root1, root2)
        contaminated_siblings = [
            o.extras.get('entity_key') for o in outputs
            if o.extras.get('incomplete')
            and o.extras.get('subsource') in ('map_sectors', 'map_zones',
                                              'highway_sec', 'highway_zone',
                                              'galaxy', 'regionyield',
                                              'regiondef')
        ]
        self.assertEqual(
            contaminated_siblings, [],
            f'sibling sub-sources were contaminated: {contaminated_siblings}',
        )


if __name__ == '__main__':
    unittest.main()
