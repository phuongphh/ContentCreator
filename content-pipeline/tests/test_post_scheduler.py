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
        self.assertEqual(ps._parse_slot_spec("sun 20:00"), (frozenset({6}), 20, 0))
        self.assertEqual(ps._parse_slot_spec("Tuesday 19:30"), (frozenset({1}), 19, 30))

    def test_weekday_range(self):
        weekdays, h, m = ps._parse_slot_spec("mon-sat 12:00")
        self.assertEqual(sorted(weekdays), [0, 1, 2, 3, 4, 5])
        self.assertEqual((h, m), (12, 0))

    def test_weekday_list(self):
        weekdays, _, _ = ps._parse_slot_spec("mon,wed,fri 9:30")
        self.assertEqual(sorted(weekdays), [0, 2, 4])

    def test_weekday_range_wraps_weekend(self):
        weekdays, _, _ = ps._parse_slot_spec("sat-mon 8:00")
        self.assertEqual(sorted(weekdays), [0, 5, 6])

    def test_bad_specs_raise(self):
        for bad in ("someday 12:00", "12:00 extra stuff", "25:00", "12:99",
                    "mon-someday 12:00"):
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
        # Cadence short = 1 slot/ngày (mon-sat 12:00) → video thứ 2 rơi sang
        # slot 12:00 NGÀY HÔM SAU (thứ 4), không phải slot thứ 2 cùng ngày.
        vid1, vid2 = _make_video(), _make_video()
        now = datetime(2026, 7, 7, 9, 0)  # thứ 3
        ps.schedule_video(vid1, "drama_youtube", now=now)
        post2 = ps.schedule_video(vid2, "drama_youtube", now=now)
        self.assertEqual(post2["scheduled_at"], "2026-07-08 12:00:00")

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


class TestAuthErrorRetry(SchedulerBase):
    """Token OAuth chết giữa chừng → requeue có giới hạn (issue #109).

    An toàn vì RefreshError xảy ra TRƯỚC videos.insert — không thể video trùng.
    """

    def _queue_due_post(self):
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")
        return vid, post_id

    @staticmethod
    def _refresh_error():
        return RuntimeError("RefreshError: ('invalid_grant: Token has been "
                            "expired or revoked.',)")

    def test_auth_error_requeues_instead_of_failing(self):
        vid, post_id = self._queue_due_post()
        with patch.object(ps, "_dispatch", side_effect=self._refresh_error()), \
             patch.object(ps, "_alert_safe") as alert:
            summary = ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
        self.assertEqual(summary["retried"], 1)
        self.assertEqual(summary["failed"], 0)
        post = sp.get_post(post_id)
        self.assertEqual(post["status"], "queued")
        self.assertEqual(post["attempts"], 1)
        # Lùi POST_AUTH_RETRY_DELAY_MINUTES phút → không retry ngay tick sau.
        self.assertGreater(post["scheduled_at"], "2026-07-07 12:02:00")
        # Alert nêu rõ token + cách cấp lại, không phải "upload thất bại" chung chung.
        alert.assert_called_once()
        msg = alert.call_args[0][0]
        self.assertIn("--force-reauth", msg)
        self.assertIn("KHÔNG mất", msg)

    def test_retry_alerts_only_once_not_every_tick(self):
        vid, post_id = self._queue_due_post()
        with patch.object(ps, "_dispatch", side_effect=self._refresh_error()), \
             patch.object(ps, "_alert_safe") as alert:
            ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
            ps.run_tick(now=datetime(2026, 7, 7, 13, 30))
        self.assertEqual(sp.get_post(post_id)["attempts"], 2)
        self.assertEqual(alert.call_count, 1)

    def test_gives_up_after_max_attempts_with_recovery_hint(self):
        vid, post_id = self._queue_due_post()
        with patch.object(ps.config, "POST_AUTH_RETRY_MAX", 1), \
             patch.object(ps, "_dispatch", side_effect=self._refresh_error()), \
             patch.object(ps, "_alert_safe") as alert:
            ps.run_tick(now=datetime(2026, 7, 7, 12, 2))          # attempt 1
            summary = ps.run_tick(now=datetime(2026, 7, 7, 13, 30))  # hết lượt
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(sp.get_post(post_id)["status"], "failed")
        final = alert.call_args[0][0]
        self.assertIn("--force-reauth", final)
        self.assertIn(f"requeue {post_id}", final)

    def test_non_auth_error_still_fails_without_retry(self):
        vid, post_id = self._queue_due_post()
        with patch.object(ps, "_dispatch", side_effect=RuntimeError("ffmpeg boom")), \
             patch.object(ps, "_alert_safe"):
            summary = ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["retried"], 0)
        self.assertEqual(sp.get_post(post_id)["status"], "failed")

    def test_missing_migration_degrades_to_old_behaviour(self):
        """Quên `storage.migrate up` → mark_failed như trước, KHÔNG sập cả tick."""
        import sqlite3 as _sqlite3
        vid, post_id = self._queue_due_post()
        with patch.object(ps, "_dispatch", side_effect=self._refresh_error()), \
             patch.object(ps.scheduled_posts, "requeue",
                          side_effect=_sqlite3.OperationalError(
                              "no such column: attempts")), \
             patch.object(ps, "_alert_safe"):
            summary = ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(sp.get_post(post_id)["status"], "failed")

    def test_never_requeues_a_post_already_live(self):
        """Chốt chặn cuối: post đã có platform_video_id không bao giờ đăng lại."""
        vid, post_id = self._queue_due_post()
        sp.claim(post_id)
        sp.record_platform_id(post_id, "abc123", "https://youtu.be/abc123")
        post = sp.get_post(post_id)
        self.assertFalse(ps._retry_after_auth_error(post, "invalid_grant"))
        self.assertEqual(sp.get_post(post_id)["status"], "uploading")


class TestRequeuePostCommand(SchedulerBase):
    """Phục hồi TAY sau khi cấp lại token (issue #109)."""

    def test_requeues_failed_post_and_resets_attempts(self):
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")
        sp.claim(post_id)
        sp.mark_failed(post_id, "invalid_grant")
        msg = ps.requeue_post(post_id, in_minutes=1,
                              now=datetime(2026, 7, 7, 15, 0))
        post = sp.get_post(post_id)
        self.assertEqual(post["status"], "queued")
        self.assertEqual(post["attempts"], 0)
        self.assertEqual(post["scheduled_at"], "2026-07-07 15:01:00")
        self.assertIn("xếp lại", msg)

    def test_refuses_stuck_uploading_post_without_force(self):
        """Post kẹt 'uploading' có thể ĐÃ lên kênh mà chưa ghi được id (Codex #110)."""
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")
        sp.claim(post_id)  # 'uploading', chưa có platform_video_id
        msg = ps.requeue_post(post_id, now=datetime(2026, 7, 7, 15, 0))
        self.assertIn("--force", msg)
        self.assertEqual(sp.get_post(post_id)["status"], "uploading")

    def test_force_allows_uploading_after_operator_checked(self):
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")
        sp.claim(post_id)
        ps.requeue_post(post_id, now=datetime(2026, 7, 7, 15, 0), force=True)
        self.assertEqual(sp.get_post(post_id)["status"], "queued")

    def test_force_still_refuses_post_already_on_platform(self):
        """--force chỉ nới trạng thái, KHÔNG bỏ qua bằng chứng đã lên sóng."""
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")
        sp.claim(post_id)
        sp.record_platform_id(post_id, "abc", "https://youtu.be/abc")
        msg = ps.requeue_post(post_id, now=datetime(2026, 7, 7, 15, 0), force=True)
        self.assertIn("ĐÃ lên platform", msg)
        self.assertEqual(sp.get_post(post_id)["status"], "uploading")

    def test_refuses_post_already_on_platform(self):
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")
        sp.claim(post_id)
        sp.record_platform_id(post_id, "abc", "https://youtu.be/abc")
        sp.mark_failed(post_id, "late failure")
        msg = ps.requeue_post(post_id, now=datetime(2026, 7, 7, 15, 0))
        self.assertIn("ĐÃ lên platform", msg)
        self.assertEqual(sp.get_post(post_id)["status"], "failed")

    def test_unknown_post(self):
        self.assertIn("Không có post", ps.requeue_post(999))


class TestDispatchYouTube(SchedulerBase):
    def test_on_uploaded_persists_platform_id_before_return(self):
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")
        sp.claim(post_id)

        recorded_during_upload = {}

        def fake_upload(video_id, channel_key, on_uploaded=None):
            on_uploaded("yt7", "https://youtu.be/yt7")
            # tại thời điểm này (giữa upload và thumbnail/caption) row đã
            # phải mang platform_video_id dù vẫn 'uploading'
            recorded_during_upload.update(sp.get_post(post_id))
            return {"youtube_video_id": "yt7", "url": "https://youtu.be/yt7"}

        with patch("publisher.youtube_uploader.upload_to_youtube",
                   side_effect=fake_upload):
            ok, url, pid = ps._dispatch(sp.get_post(post_id))
        self.assertTrue(ok)
        self.assertEqual(recorded_during_upload["platform_video_id"], "yt7")
        self.assertEqual(recorded_during_upload["status"], "uploading")

    def test_platform_id_survives_late_failure(self):
        # Upload xong (on_uploaded đã bắn) nhưng bước sau chết → post failed
        # nhưng vẫn giữ bằng chứng video đã live.
        vid = _make_video()
        post_id = sp.insert_post(vid, "drama_youtube", "2026-07-07 12:00:00")

        def fake_upload(video_id, channel_key, on_uploaded=None):
            on_uploaded("yt8", "https://youtu.be/yt8")
            raise RuntimeError("died after upload")

        with patch("publisher.youtube_uploader.upload_to_youtube",
                   side_effect=fake_upload), \
             patch.object(ps, "_alert_safe"):
            summary = ps.run_tick(now=datetime(2026, 7, 7, 12, 2))
        self.assertEqual(summary["failed"], 1)
        post = sp.get_post(post_id)
        self.assertEqual(post["status"], "failed")
        self.assertEqual(post["platform_video_id"], "yt8")


class TestDispatchTikTok(SchedulerBase):
    """TikTok = gửi Telegram (Bé MC), KHÔNG auto-upload. Nhánh này chỉ chạy nếu
    còn post tiktok cũ (routing mới không tạo post tiktok nữa)."""

    def test_tiktok_sends_to_be_mc_telegram(self):
        vid = _make_video(destination=None)
        post_id = sp.insert_post(vid, "tiktok_main", "2026-07-07 12:00:00")
        with patch("notifier.telegram_bot.send_tiktok_manual",
                   return_value=True) as send:
            ok, url, pid = ps._dispatch(sp.get_post(post_id))
        self.assertTrue(ok)
        self.assertEqual(url, "telegram://be_mc")
        self.assertIsNone(pid)
        send.assert_called_once_with(vid)

    def test_tiktok_send_failure_returns_error(self):
        vid = _make_video(destination=None)
        post_id = sp.insert_post(vid, "tiktok_main", "2026-07-07 12:00:00")
        with patch("notifier.telegram_bot.send_tiktok_manual",
                   return_value=False):
            ok, url, pid = ps._dispatch(sp.get_post(post_id))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
