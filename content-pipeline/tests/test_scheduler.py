"""Tests for publisher.scheduler — content schedule (short Mon-Sat, long Sun)."""
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

    def test_sunday_is_long(self):
        s = get_today_schedule(SUN)
        self.assertEqual(s["video_type"], "long")
        self.assertEqual(s["platforms"], ["youtube"])

    def test_weekday_pattern_short_mon_to_sat(self):
        for d in (MON, TUE, WED, THU, FRI, SAT):
            with self.subTest(day=d):
                self.assertEqual(get_today_schedule(d)["video_type"], "short")

    def test_no_day_off(self):
        # Không còn ngày nghỉ — mọi ngày đều có lịch sản xuất.
        for d in (MON, TUE, WED, THU, FRI, SAT, SUN):
            with self.subTest(day=d):
                self.assertIsNotNone(get_today_schedule(d))

    def test_scheduled_date_echoed(self):
        s = get_today_schedule(WED)
        self.assertEqual(s["scheduled_date"], WED.isoformat())


class TestGetNextScheduledDate(unittest.TestCase):
    def test_next_of_monday_is_tuesday_short(self):
        next_date, schedule = get_next_scheduled_date(MON)
        self.assertEqual(next_date, TUE)
        self.assertEqual(schedule["video_type"], "short")

    def test_saturday_next_is_sunday_long(self):
        next_date, schedule = get_next_scheduled_date(SAT)
        self.assertEqual(next_date, SUN)
        self.assertEqual(schedule["video_type"], "long")

    def test_sunday_next_is_monday_short(self):
        next_date, schedule = get_next_scheduled_date(SUN)
        self.assertEqual(next_date, date(2024, 1, 8))
        self.assertEqual(next_date.weekday(), 0)
        self.assertEqual(schedule["video_type"], "short")


class TestGetPlatformLabel(unittest.TestCase):
    def test_known_labels(self):
        self.assertEqual(get_platform_label("youtube"), "YouTube")
        self.assertEqual(get_platform_label("youtube_shorts"), "YouTube Shorts")
        self.assertEqual(get_platform_label("tiktok"), "TikTok")

    def test_unknown_passthrough(self):
        self.assertEqual(get_platform_label("instagram"), "instagram")


if __name__ == "__main__":
    unittest.main()
