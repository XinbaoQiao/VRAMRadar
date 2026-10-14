"""Cache cleanup profile settings and safe cleanup summary."""
from __future__ import annotations

import unittest

from vram_radar.models import Profile
from vram_radar import housekeeping as hk


class CacheCleanupSettingsTests(unittest.TestCase):
    def test_defaults_match_daily_housekeeper(self):
        profile = Profile.empty("default")
        self.assertTrue(profile.cache_cleanup_enabled)
        self.assertEqual(profile.cache_cleanup_interval_hours, 24)

    def test_round_trip_hours(self):
        raw = Profile.empty("default").to_dict()
        raw["cache_cleanup_enabled"] = False
        raw["cache_cleanup_interval_hours"] = 12
        profile = Profile.from_dict(raw, expected_id="default")
        self.assertFalse(profile.cache_cleanup_enabled)
        self.assertEqual(profile.cache_cleanup_interval_hours, 12)
        again = Profile.from_dict(profile.to_dict(), expected_id="default")
        self.assertEqual(again.cache_cleanup_interval_hours, 12)

    def test_rejects_out_of_range_hours(self):
        raw = Profile.empty("default").to_dict()
        raw["cache_cleanup_interval_hours"] = 200
        with self.assertRaises(Exception):
            Profile.from_dict(raw, expected_id="default")

    def test_summarize_cleanup_ignores_failures(self):
        summary = {
            "logs": 2,
            "temporaries": "failed:OSError",
            "bytes_before": {"logs": 1000, "cache": 2000, "config": 100},
            "bytes": {"logs": 400, "cache": 1500, "config": 100},
        }
        totals = hk.summarize_cleanup(summary)
        self.assertEqual(totals["removed_items"], 2)
        self.assertEqual(totals["bytes_freed"], 1100)


if __name__ == "__main__":
    unittest.main()
