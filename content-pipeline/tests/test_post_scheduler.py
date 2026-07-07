"""Tests for scheduler/post_scheduler.py (Phase 5 — cadence queue + tick)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.scheduled_posts as sp
import scheduler.post_scheduler as ps


def _make_video(**overrides) -> int:
    fields = dict(video_type="short", script_text="x", track="drama",
                  destination="drama_youtube")
    fields.update(overrides)
    return db.insert_video(**fields)


class SchedulerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestSlotParsing(unittest.TestCase):
    def test_daily_slot(self):
        self.assertEqual(ps._parse_slot_spec("21:00"), (None, 21, 0))

    def test_weekly_slot(self):
        self.assertEqual(ps._parse_slot_spec("sun 20:00"), (6, 20, 0))
        self.assertEqual(ps._parse_slot_spec("Tuesday 19:30"), (1, 19, 30))

    def test_bad_specs_raise(self):
        for bad in ("someday 12:00", "12:00 extra stuff", "25:00", "12:99"):
            with self.assertRaises(ValueError):
                ps._parse_slot_spec(bad)

    def test_all_cadence_entries_parse(self):
        # Bắt lỗi typo trong CADENCE ngay ở test thay vì lúc runtime.
        for specs in ps.CADENCE.values():
            for spec in specs:
                ps._parse_slot_spec(spec)


class TestIterSlots(unittest.TestCase):
    def test_daily_slots_strictly_after(self):
        after = datetime(2026, 7, 7, 12, 0)  # đúng 12:00 → slot 12:00 hôm nay bị loại
        slots = ps.iter_slots(["12:00", "21:00"], after, days=1)
        self.assertEqual(slots[0], datetime(2026, 7, 7, 21, 0))
        self.assertEqual(slots[1], datetime(2026, 7, 8, 12, 0))

    def test_weekly_slot(self):
        after = datetime(2026, 7, 7, 8, 0)  # thứ 3
        slots = ps.iter_slots(["sun 20:00"], after, days=8)
        self.assertEqual(slots[0], datetime(2026, 7, 12, 20, 0))


class TestScheduleVideo(SchedulerBase):
    def test_schedules_next_free_slot(self):
        vid = _make_video()
        now = datetime(2026, 7, 7, 9, 0)
        post = ps.schedule_video(vid, "drama_youtube", now=now)
        self.assertEqual(post["scheduled_at"], "2026-07-07 12:00:00")

    def test_taken_slot_moves_to_next(self):
        vid1, vid2 = _make_video(), _make_video()
        now = datetime(2026, 7, 7, 9, 0)
        ps.schedule_video(vid1, "drama_youtube", now=now)
        post2 = ps.schedule_video(vid2, "drama_youtube", now=now)
        self.assertEqual(post2["scheduled_at"], "2026-07-07 21:00:00")

    def test_idempotent_per_video_channel(self):
        vid = _make_video()
        now = datetime(2026, 7, 7, 9, 0)
        post1 = ps.schedule_video(vid, "drama_youtube", now=now)
        post2 = ps.schedule_video(vid, "drama_youtube", now=now)
        self.assertEqual(post1["id"], post2["id"])

    def test_unknown_channel_raises(self):
        vid = _make_video()
        with self.assertRaises(ValueError):
            ps.schedule_video(vid, "nonexistent_channel")

    def test_long_video_weekly_cadence(self):
        vid = _make_video(video_type="long")
        now = datetime(2026, 7, 7, 9, 0)  # thứ 3
        post = ps.schedule_video(vid, "drama_youtube", now=now)
        self.assertEqual(post["scheduled_at"], "2026-07-12 20:00:00")  # CN 20:00


class TestRunTick(SchedulerBase):
    def _queue_due_post(self):
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")
        return vid, post_id

    def test_uploads_due_post(self):
        vid, post_id = self._queue_due_post()
        with patch.object(ps, "_dispatch",
                          return_value=(True, "https://youtu.be/abc", "abc")) as d, \
             patch.object(ps, "_notify_published_safe"), \
             patch.object(ps, "_alert_safe"):
            summary = ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
        self.assertEqual(summary["uploaded"], 1)
        d.assert_called_once()
        post = sp.get_post(post_id)
        self.assertEqual(post["status"], "done")
        self.assertEqual(post["platform_video_id"], "abc")
        self.assertEqual(db.get_video(vid)["status"], "published")
        self.assertEqual(db.get_video(vid)["publish_url"], "https://youtu.be/abc")

    def test_no_double_upload_on_second_tick(self):
        self._queue_due_post()
        with patch.object(ps, "_dispatch",
                          return_value=(True, "u", "id")) as d, \
             patch.object(ps, "_notify_published_safe"), \
             patch.object(ps, "_alert_safe"):
            ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
            summary2 = ps.run_tick(now=datetime(2026, 7, 7, 12, 7))
        self.assertEqual(d.call_count, 1)
        self.assertEqual(summary2["uploaded"], 0)

    def test_failed_dispatch_marks_failed_and_alerts(self):
        vid, post_id = self._queue_due_post()
        with patch.object(ps, "_dispatch", return_value=(False, "boom", None)), \
             patch.object(ps, "_alert_safe") as alert:
            summary = ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(sp.get_post(post_id)["status"], "failed")
        alert.assert_called_once()

    def test_dispatch_exception_does_not_stop_tick(self):
        vid1, _ = self._queue_due_post()
        vid2 = _make_video()
        sp.insert_post(vid2, "drama_youtube", "2026-07-07 12:30:00")
        with patch.object(ps, "_dispatch",
                          side_effect=[RuntimeError("x"), (True, "u", "i")]), \
             patch.object(ps, "_notify_published_safe"), \
             patch.object(ps, "_alert_safe"):
            summary = ps.run_tick(now=datetime(2026, 7, 7, 13, 0))
        self.assertEqual(summary["uploaded"], 1)
        self.assertEqual(summary["failed"], 1)

    def test_stale_uploading_alerts_but_never_retries(self):
        vid, post_id = self._queue_due_post()
        sp.claim(post_id)
        conn = db.get_connection()
        conn.execute("UPDATE scheduled_posts SET updated_at = '2020-01-01 00:00:00' "
                     "WHERE id = ?", (post_id,))
        conn.commit()
        conn.close()
        with patch.object(ps, "_dispatch") as d, \
             patch.object(ps, "_alert_safe") as alert:
            summary = ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
        self.assertEqual(summary["stale"], 1)
        d.assert_not_called()  # video có thể ĐÃ lên kênh — không tự đăng lại
        alert.assert_called_once()
        self.assertEqual(sp.get_post(post_id)["status"], "uploading")


class TestDispatchTikTok(SchedulerBase):
    def test_tiktok_without_token_exports_manual_queue(self):
        vid = _make_video(destination=None)
        post_id = sp.insert_post(vid, "tiktok_main", "2026-07-07 12:00:00")
        with patch.object(ps.config, "TIKTOK_ACCESS_TOKEN", ""), \
             patch("publisher.tiktok_manual.export_for_manual_upload",
                   return_value="/queue/video_1.mp4") as exp:
            ok, url, pid = ps._dispatch(sp.get_post(post_id))
        self.assertTrue(ok)
        self.assertEqual(url, "file:///queue/video_1.mp4")
        exp.assert_called_once_with(vid)

    def test_tiktok_with_token_uses_api(self):
        vid = _make_video(destination=None)
        db.update_video_paths(vid, video_path="/tmp/v.mp4")
        post_id = sp.insert_post(vid, "tiktok_main", "2026-07-07 12:00:00")
        with patch.object(ps.config, "TIKTOK_ACCESS_TOKEN", "tok"), \
             patch("publisher.tiktok_uploader.upload_video",
                   return_value="pub42") as up:
            ok, url, pid = ps._dispatch(sp.get_post(post_id))
        self.assertTrue(ok)
        self.assertEqual(pid, "pub42")
        up.assert_called_once()


if __name__ == "__main__":
    unittest.main()
