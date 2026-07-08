"""Tests for analytics/youtube_puller.py using injected fake API services."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.video_metrics as vm
import storage.channel_metrics as cm
from analytics import youtube_puller as yp


class _Req:
    def __init__(self, resp):
        self._r = resp

    def execute(self):
        return self._r


class _Reports:
    def query(self, **kw):
        dims = kw.get("dimensions", "")
        if dims == "video":
            return _Req({
                "columnHeaders": [{"name": n} for n in
                                  ("video", "views", "likes", "comments", "shares",
                                   "estimatedMinutesWatched", "averageViewDuration")],
                "rows": [["vidA", 1000, 50, 10, 5, 300.0, 45.0],
                         ["vidB", 200, 5, 1, 0, 20.0, 30.0]],
            })
        if dims == "elapsedVideoTimeRatio":
            return _Req({
                "columnHeaders": [{"name": "elapsedVideoTimeRatio"},
                                  {"name": "audienceWatchRatio"}],
                "rows": [[0.0, 1.0], [0.5, 0.62], [1.0, 0.2]],
            })
        return _Req({  # channel totals
            "columnHeaders": [{"name": "subscribersGained"},
                              {"name": "subscribersLost"}, {"name": "views"}],
            "rows": [[30, 5, 1200]],
        })


class _Analytics:
    def reports(self):
        return _Reports()


class _Channels:
    def list(self, **kw):
        return _Req({"items": [{"statistics": {"subscriberCount": "1500"}}]})


class _Data:
    def channels(self):
        return _Channels()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestPull(Base):
    def test_pull_stores_video_and_channel_metrics(self):
        n = yp.pull_metrics_for_channel("ai_youtube", analytics=_Analytics(),
                                        data=_Data(), snapshot_date="2026-07-08")
        self.assertEqual(n, 2)
        latest = {r["external_id"]: r for r in vm.latest_per_video()}
        self.assertEqual(latest["vidA"]["views"], 1000)
        self.assertEqual(latest["vidA"]["retention_50_pct"], 62.0)  # ratio 0.5 → 62%
        self.assertEqual(latest["vidA"]["watch_time_minutes"], 300.0)

        ch = cm.get_range("ai_youtube")
        self.assertEqual(ch[-1]["subscribers"], 1500)
        self.assertEqual(ch[-1]["subscribers_gained"], 25)  # 30 gained - 5 lost

    def test_with_retention_false_skips_retention(self):
        yp.pull_metrics_for_channel("ai_youtube", with_retention=False,
                                    analytics=_Analytics(), data=_Data())
        latest = vm.latest_per_video()
        self.assertTrue(all(r["retention_50_pct"] is None for r in latest))

    def test_non_youtube_channel_returns_zero(self):
        self.assertEqual(
            yp.pull_metrics_for_channel("tiktok_main", analytics=_Analytics(),
                                        data=_Data()),
            0,
        )


if __name__ == "__main__":
    unittest.main()
