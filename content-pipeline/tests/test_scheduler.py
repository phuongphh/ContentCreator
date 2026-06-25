"""Tests for publisher.scheduler — dual-format publishing schedule."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from publisher.scheduler import (
    get_today_schedule,
    get_next_scheduled_date,
    get_platform_label,
)

# 2024-01-01 is a Monday, so 1..7 Jan map cleanly to Mon..Sun.
MON = date(2024, 1, 1)
TUE = date(2024, 1, 2)
WED = date(2024, 1, 3)
THU = date(2024, 1, 4)
FRI = date(2024, 1, 5)
SAT = date(2024, 1, 6)
SUN = date(2024, 1, 7)


class TestGetTodaySchedule(unittest.TestCase):
    def test_monday_is_short(self):
        s = get_today_schedule(MON)
        self.assertEqual(s["video_type"], "short")
        self.assertEqual(s["platforms"], ["youtube_shorts", "tiktok"])

    def test_tuesday_is_long(self):
        s = get_today_schedule(TUE)
        self.assertEqual(s["video_type"], "long")
        self.assertEqual(s["platforms"], ["youtube"])

    def test_weekday_pattern(self):
        expected = {
            MON: "short", TUE: "long", WED: "short",
            THU: "long", FRI: "short", SAT: "long",
        }
        for d, vtype in expected.items():
            with self.subTest(day=d):
                self.assertEqual(get_today_schedule(d)["video_type"], vtype)

    def test_sunday_is_off(self):
        self.assertIsNone(get_today_schedule(SUN))

    def test_scheduled_date_echoed(self):
        s = get_today_schedule(WED)
        self.assertEqual(s["scheduled_date"], WED.isoformat())


class TestGetNextScheduledDate(unittest.TestCase):
    def test_saturday_skips_sunday_to_monday(self):
        # From Sat Jan 6: Sun Jan 7 is off, so next is the following Mon (Jan 8).
        next_date, schedule = get_next_scheduled_date(SAT)
        self.assertEqual(next_date, date(2024, 1, 8))
        self.assertEqual(next_date.weekday(), 0)  # Monday
        self.assertEqual(schedule["video_type"], "short")

    def test_returns_next_working_day(self):
        next_date, schedule = get_next_scheduled_date(MON)
        self.assertEqual(next_date, TUE)
        self.assertEqual(schedule["video_type"], "long")

    def test_never_returns_sunday(self):
        for d in [MON, TUE, WED, THU, FRI, SAT, SUN]:
            with self.subTest(day=d):
                next_date, _ = get_next_scheduled_date(d)
                self.assertNotEqual(next_date.weekday(), 6)


class TestGetPlatformLabel(unittest.TestCase):
    def test_known_labels(self):
        self.assertEqual(get_platform_label("youtube"), "YouTube")
        self.assertEqual(get_platform_label("youtube_shorts"), "YouTube Shorts")
        self.assertEqual(get_platform_label("tiktok"), "TikTok")

    def test_unknown_passthrough(self):
        self.assertEqual(get_platform_label("instagram"), "instagram")


if __name__ == "__main__":
    unittest.main()
