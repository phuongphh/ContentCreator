"""Tests for notifier/review_bot.py (Phase 5 — Telegram Review Gate)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import notifier.review_bot as rb


class ReviewBotBase(unittest.TestCase):
    """Temp DB + temp state file cho mỗi test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patches = [
            patch.object(db.config, "DB_PATH", self.dbpath),
            patch.object(rb, "_STATE_FILE", os.path.join(self.tmp, "state.json")),
        ]
        for p in self._patches:
            p.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _make_pending_video(self, **overrides):
        fields = dict(video_type="short", script_text="kịch bản", track="drama",
                      destination="drama_youtube", youtube_title="Tiêu đề")
        fields.update(overrides)
        video_id = db.insert_video(**fields)
        db.update_video_status(video_id, "pending_approval")
        return video_id


class TestHandleCallbackParsing(ReviewBotBase):
    def test_garbage_data(self):
        for bad in ("", "xx:a:1", "rv:a", "rv:a:notanumber", None):
            reply, kb = rb.handle_callback(bad)
            self.assertIn("không hợp lệ", reply)
            self.assertIsNone(kb)


class TestApprove(ReviewBotBase):
    def test_approve_routes_all_destinations(self):
        video_id = self._make_pending_video()
        with patch.object(rb, "_route_to_channel",
                          side_effect=lambda v, k, c: f"  ok {k}") as route:
            reply, kb = rb.handle_callback(f"rv:a:{video_id}")
        self.assertIn("đã duyệt", reply)
        routed = [c.args[1] for c in route.call_args_list]
        # drama short → kênh đích + TikTok (account mixed nhận track drama)
        self.assertEqual(routed, ["drama_youtube", "tiktok_main"])
        self.assertEqual(db.get_video(video_id)["status"], "approved")

    def test_double_approve_blocked(self):
        video_id = self._make_pending_video()
        with patch.object(rb, "_route_to_channel", return_value="  ok"):
            rb.handle_callback(f"rv:a:{video_id}")
            reply, _ = rb.handle_callback(f"rv:a:{video_id}")
        self.assertIn("không ở trạng thái chờ duyệt", reply)

    def test_approve_missing_video(self):
        reply, _ = rb.handle_callback("rv:a:999")
        self.assertIn("không tồn tại", reply)

    def test_routing_error_reported_not_raised(self):
        video_id = self._make_pending_video()
        with patch.object(rb, "_route_to_channel", side_effect=RuntimeError("db down")):
            reply, _ = rb.handle_callback(f"rv:a:{video_id}")
        self.assertIn("lỗi xếp lịch", reply)


class TestRouteToChannel(ReviewBotBase):
    def test_tiktok_routes_to_be_mc_telegram(self):
        # TikTok = gửi video qua Telegram (Bé MC), KHÔNG auto-schedule.
        video_id = self._make_pending_video()
        video = db.get_video(video_id)
        from channels import get_channel
        with patch("notifier.telegram_bot.send_tiktok_manual",
                   return_value=True) as send:
            line = rb._route_to_channel(video, "tiktok_main", get_channel("tiktok_main"))
        self.assertIn("Telegram", line)
        send.assert_called_once_with(video_id)

    def test_tiktok_send_failure_reported(self):
        video_id = self._make_pending_video()
        video = db.get_video(video_id)
        from channels import get_channel
        with patch("notifier.telegram_bot.send_tiktok_manual", return_value=False):
            line = rb._route_to_channel(video, "tiktok_main", get_channel("tiktok_main"))
        self.assertIn("❌", line)

    def test_youtube_schedules(self):
        video_id = self._make_pending_video()
        video = db.get_video(video_id)
        from channels import get_channel
        with patch("scheduler.post_scheduler.schedule_video",
                   return_value={"scheduled_at": "2026-07-08 12:00:00"}) as sched:
            line = rb._route_to_channel(video, "drama_youtube", get_channel("drama_youtube"))
        self.assertIn("2026-07-08 12:00:00", line)
        sched.assert_called_once_with(video_id, "drama_youtube")


class TestReject(ReviewBotBase):
    def test_reject_sets_status_and_awaits_reason(self):
        video_id = self._make_pending_video()
        reply, _ = rb.handle_callback(f"rv:r:{video_id}")
        self.assertIn("đã loại", reply)
        self.assertEqual(db.get_video(video_id)["status"], "rejected")
        # lý do được lưu vào review_note
        followup = rb.handle_awaiting_message("hook quá yếu")
        self.assertIn("Đã lưu lý do", followup)
        self.assertEqual(db.get_video(video_id)["review_note"], "hook quá yếu")

    def test_skip_clears_awaiting(self):
        video_id = self._make_pending_video()
        rb.handle_callback(f"rv:r:{video_id}")
        self.assertIsNotNone(rb.skip_awaiting())
        # sau skip, message thường không còn bị nuốt bởi review FSM
        self.assertIsNone(rb.handle_awaiting_message("tin nhắn khác"))

    def test_skip_without_state(self):
        self.assertIsNone(rb.skip_awaiting())


class TestEditMetadata(ReviewBotBase):
    def test_edit_menu_lists_fields(self):
        video_id = self._make_pending_video()
        reply, kb = rb.handle_callback(f"rv:e:{video_id}")
        self.assertIsNotNone(kb)
        datas = [btn["callback_data"] for row in kb["inline_keyboard"] for btn in row]
        self.assertIn(f"rv:ef:{video_id}:title", datas)

    def test_edit_field_flow_updates_db(self):
        video_id = self._make_pending_video()
        reply, _ = rb.handle_callback(f"rv:ef:{video_id}:title")
        self.assertIn("Tiêu đề", reply)
        followup = rb.handle_awaiting_message("Tiêu đề mới hay hơn")
        self.assertIn("Đã cập nhật", followup)
        self.assertEqual(db.get_video(video_id)["youtube_title"], "Tiêu đề mới hay hơn")

    def test_unknown_field_rejected(self):
        video_id = self._make_pending_video()
        reply, _ = rb.handle_callback(f"rv:ef:{video_id}:status")
        self.assertIn("không hợp lệ", reply)

    def test_state_survives_restart(self):
        # State ghi ra file — module "restart" (đọc lại file) vẫn thấy.
        video_id = self._make_pending_video()
        rb.handle_callback(f"rv:ef:{video_id}:cap")
        followup = rb.handle_awaiting_message("caption mới")
        self.assertIn("Đã cập nhật", followup)
        self.assertEqual(db.get_video(video_id)["tiktok_caption"], "caption mới")


class TestDestinationsFor(ReviewBotBase):
    def test_drama_short_gets_youtube_and_tiktok(self):
        video = {"track": "drama", "video_type": "short",
                 "destination": "drama_youtube"}
        self.assertEqual(rb._destinations_for(video),
                         ["drama_youtube", "tiktok_main"])

    def test_long_video_not_duplicated_to_tiktok(self):
        video = {"track": "drama", "video_type": "long",
                 "destination": "drama_youtube"}
        self.assertEqual(rb._destinations_for(video), ["drama_youtube"])

    def test_no_destination_falls_back_to_track(self):
        video = {"track": "ai", "video_type": "short", "destination": None}
        dests = rb._destinations_for(video)
        self.assertIn("ai_youtube", dests)
        self.assertIn("tiktok_main", dests)
        self.assertNotIn("drama_youtube", dests)


class TestApproveBadDestination(ReviewBotBase):
    def test_stale_destination_reports_error_and_routes_rest(self):
        # destination không còn trong registry — không được nổ cả handler
        # sau khi đã claim 'approved' (finding Codex PR #70).
        video_id = self._make_pending_video(destination="ghost_channel")
        with patch.object(rb, "_route_to_channel",
                          side_effect=lambda v, k, c: f"  ok {k}") as route:
            reply, _ = rb.handle_callback(f"rv:a:{video_id}")
        self.assertIn("đã duyệt", reply)
        self.assertIn("ghost_channel: lỗi xếp lịch", reply)
        self.assertIn("Xếp tay:", reply)
        # kênh còn lại (tiktok_main cho track drama) vẫn được route
        routed = [c.args[1] for c in route.call_args_list]
        self.assertEqual(routed, ["tiktok_main"])


class TestPushReview(ReviewBotBase):
    def _make_ready_video_with_file(self):
        video_id = self._make_pending_video()
        db.update_video_status(video_id, "ready")
        src = os.path.join(self.tmp, f"v_{video_id}.mp4")
        with open(src, "wb") as f:
            f.write(b"mp4")
        db.update_video_paths(video_id, video_path=src)
        return video_id

    def test_video_sent_sets_pending(self):
        video_id = self._make_ready_video_with_file()
        import notifier.telegram_bot as tb
        with patch.object(tb, "_send_text", return_value=True), \
             patch.object(tb, "_send_video_file", return_value="55"), \
             patch("video.preview.compress_for_preview", side_effect=lambda p: p):
            self.assertTrue(rb.push_review(video_id))
        self.assertEqual(db.get_video(video_id)["status"], "pending_approval")

    def test_fallback_keyboard_delivered_sets_pending(self):
        video_id = self._make_ready_video_with_file()
        import notifier.telegram_bot as tb
        with patch.object(tb, "_send_text", return_value=True), \
             patch.object(tb, "_send_video_file", return_value=None), \
             patch.object(tb, "send_message_with_keyboard", return_value=True) as kb, \
             patch("video.preview.compress_for_preview", side_effect=lambda p: p):
            self.assertTrue(rb.push_review(video_id))
        kb.assert_called_once()
        self.assertEqual(db.get_video(video_id)["status"], "pending_approval")

    def test_no_keyboard_delivered_stays_ready(self):
        # Script tới nhưng KHÔNG có keyboard nào tới tay reviewer → không được
        # đánh dấu pending_approval (script suông không bấm ✅ được) — giữ
        # 'ready' để _repush_stuck_reviews thử lại (finding Codex PR #70).
        video_id = self._make_ready_video_with_file()
        import notifier.telegram_bot as tb
        with patch.object(tb, "_send_text", return_value=True), \
             patch.object(tb, "_send_video_file", return_value=None), \
             patch.object(tb, "send_message_with_keyboard", return_value=False), \
             patch("video.preview.compress_for_preview", side_effect=lambda p: p):
            self.assertFalse(rb.push_review(video_id))
        self.assertEqual(db.get_video(video_id)["status"], "ready")


class TestLegacyCommandRouting(ReviewBotBase):
    """Lệnh /approve_<id> cũ trên video review-gate phải đi qua scheduler
    routing thay vì publish ngay (finding Codex PR #70)."""

    def _dispatch(self, text, publish_callback=None):
        import notifier.telegram_bot as tb
        from unittest.mock import MagicMock
        update = {"message": {"text": text, "chat": {"id": 123}}}
        with patch.object(tb.config, "TELEGRAM_CHAT_ID", "123"), \
             patch.object(tb, "_send_text") as send:
            tb._handle_update(update, publish_callback or MagicMock())
        return send

    def test_legacy_approve_on_gated_video_routes_to_review_bot(self):
        video_id = self._make_pending_video()  # destination=drama_youtube
        with patch("notifier.review_bot.handle_callback",
                   return_value=("routed!", None)) as cb:
            send = self._dispatch(f"/approve_{video_id}")
        cb.assert_called_once_with(f"rv:a:{video_id}")
        send.assert_called_once_with("routed!")

    def test_legacy_reject_on_gated_video_routes_to_review_bot(self):
        video_id = self._make_pending_video()
        with patch("notifier.review_bot.handle_callback",
                   return_value=("rejected!", None)) as cb:
            self._dispatch(f"/reject_{video_id}")
        cb.assert_called_once_with(f"rv:r:{video_id}")

    def test_legacy_approve_on_ai_video_keeps_old_publish_flow(self):
        video_id = db.insert_video(video_type="short", script_text="x")  # no destination
        db.update_video_status(video_id, "pending_approval")
        publish = MagicMock()
        with patch("notifier.review_bot.handle_callback") as cb:
            self._dispatch(f"/approve_{video_id}", publish_callback=publish)
        cb.assert_not_called()
        publish.assert_called_once_with(video_id)


class TestAwaitingMessageNoState(ReviewBotBase):
    def test_returns_none_so_seed_bot_can_handle(self):
        self.assertIsNone(rb.handle_awaiting_message("text bất kỳ"))


class TestTelegramCallbackDispatch(ReviewBotBase):
    """_handle_callback_query trong telegram_bot phải answer + gửi reply."""

    def test_dispatch_answers_and_replies(self):
        import notifier.telegram_bot as tb
        cq = {"id": "cb1", "data": "rv:a:1",
              "message": {"chat": {"id": 123}}}
        with patch.object(tb.config, "TELEGRAM_CHAT_ID", "123"), \
             patch.object(tb, "_answer_callback_query") as ans, \
             patch.object(tb, "_send_text") as send, \
             patch("notifier.review_bot.handle_callback",
                   return_value=("done", None)):
            tb._handle_callback_query(cq)
        # issue #88: ack ngay với toast "đang xử lý" TRƯỚC bước xử lý nặng để
        # tránh 400 "query too old"; callback_id vẫn là đối số đầu.
        ans.assert_called_once()
        self.assertEqual(ans.call_args.args[0], "cb1")
        send.assert_called_once_with("done")

    def test_wrong_chat_ignored(self):
        import notifier.telegram_bot as tb
        cq = {"id": "cb1", "data": "rv:a:1",
              "message": {"chat": {"id": 666}}}
        with patch.object(tb.config, "TELEGRAM_CHAT_ID", "123"), \
             patch.object(tb, "_answer_callback_query") as ans, \
             patch.object(tb, "_send_text") as send:
            tb._handle_callback_query(cq)
        ans.assert_called_once()
        send.assert_not_called()

    def test_keyboard_reply_uses_keyboard_sender(self):
        import notifier.telegram_bot as tb
        kb = {"inline_keyboard": []}
        cq = {"id": "cb1", "data": "rv:e:1",
              "message": {"chat": {"id": 123}}}
        with patch.object(tb.config, "TELEGRAM_CHAT_ID", "123"), \
             patch.object(tb, "_answer_callback_query"), \
             patch.object(tb, "send_message_with_keyboard") as send_kb, \
             patch("notifier.review_bot.handle_callback",
                   return_value=("chọn field", kb)):
            tb._handle_callback_query(cq)
        send_kb.assert_called_once_with("chọn field", kb)


if __name__ == "__main__":
    unittest.main()
