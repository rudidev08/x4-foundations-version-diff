#!/usr/bin/env python3
"""
Test x4_rules_chunk_profiles — default chunk-complexity thresholds and overrides.

Run:
    python3 src/x4_rules_chunk_profiles.test.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from x4_rules_chunk_profiles import (  # noqa: E402
    DEFAULT_CHUNK_PROFILE,
    chunk_profile_for_source_path,
    complexity_score,
)


class ChunkProfilesTest(unittest.TestCase):
    def test_default_profile_applies_to_normal_files(self):
        profile = chunk_profile_for_source_path("libraries/wares.xml")
        self.assertEqual(profile, DEFAULT_CHUNK_PROFILE)

    def test_jobs_override_applies_after_dlc_normalization(self):
        profile = chunk_profile_for_source_path("extensions/ego_dlc_terran/libraries/jobs.xml")
        self.assertEqual(profile.max_entities_per_chunk, 3)
        self.assertLess(profile.max_complexity_score, DEFAULT_CHUNK_PROFILE.max_complexity_score)

    def test_complexity_score_counts_subparts_after_the_first(self):
        profile = DEFAULT_CHUNK_PROFILE
        base = complexity_score(
            profile=profile,
            entity_count=1,
            changed_line_count=10,
            hunk_count=1,
            subpart_count=1,
        )
        split = complexity_score(
            profile=profile,
            entity_count=1,
            changed_line_count=10,
            hunk_count=1,
            subpart_count=3,
        )
        self.assertGreater(split, base)


if __name__ == "__main__":
    unittest.main()
