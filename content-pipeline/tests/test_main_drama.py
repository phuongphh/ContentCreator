"""Tests for main_drama.py (Phase 5 — Drama orchestrator)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import main_drama


class TestBuildNarration(unittest.TestCase):
    def setUp(self):
        # The subscribe-CTA guarantee is tested separately (TestNarrationCta);
        # blank it here so the dedupe/ordering assertions stay byte-exact.
        self._cta = patch.object(main_drama.config, "DRAMA_SUBSCRIBE_CTA", "")
        self._cta.start()
        self.addCleanup(self._cta.stop)

    def test_joins_hook_script_commentary(self):
        rewrite = {"hook": "Hook sốc!", "script": "Chuyện là thế này...",
                   "vn_commentary": "Ở Việt Nam mình..."}
        narration = main_drama.build_narration(rewrite)
        self.assertEqual(
            narration,
            "Hook sốc!\n\nChuyện là thế này...\n\nỞ Việt Nam mình...")

    def test_no_duplicate_when_script_contains_hook(self):
        rewrite = {"hook": "Hook sốc!",
                   "script": "Hook sốc! Chuyện là thế này...",
                   "vn_commentary": "Bình luận."}
        narration = main_drama.build_narration(rewrite)
        self.assertEqual(narration.count("Hook sốc!"), 1)

    def test_no_duplicate_commentary(self):
        rewrite = {"hook": "H", "script": "Chuyện. Bình luận góc nhìn Việt.",
                   "vn_commentary": "Bình luận góc nhìn Việt."}
        narration = main_drama.build_narration(rewrite)
        self.assertEqual(narration.count("Bình luận góc nhìn Việt."), 1)

    def test_empty_rewrite(self):
        self.assertEqual(main_drama.build_narration({}), "")

    def test_reactions_placed_between_script_and_commentary(self):
        rewrite = {"hook": "Hook!", "script": "Câu chuyện chính.",
                   "vn_reactions": "Cư dân mạng phán bên kia quá đáng thật.",
                   "vn_commentary": "Góc nhìn của mình là..."}
        narration = main_drama.build_narration(rewrite)
        self.assertEqual(
            narration,
            "Hook!\n\nCâu chuyện chính.\n\n"
            "Cư dân mạng phán bên kia quá đáng thật.\n\nGóc nhìn của mình là...")

    def test_missing_reactions_is_backward_compatible(self):
        # Stories with no comments have no vn_reactions -> narration unchanged.
        rewrite = {"hook": "Hook!", "script": "Câu chuyện.",
                   "vn_commentary": "Bình luận."}
        self.assertEqual(main_drama.build_narration(rewrite),
                         "Hook!\n\nCâu chuyện.\n\nBình luận.")

    def test_empty_reactions_skipped(self):
        rewrite = {"hook": "H", "script": "S", "vn_reactions": "",
                   "vn_commentary": "C"}
        self.assertEqual(main_drama.build_narration(rewrite), "H\n\nS\n\nC")

    def test_hook_repeated_with_different_punctuation_not_duplicated(self):
        # The "title read twice" bug: the model restates the hook as the
        # script's opening line with only punctuation/case differences, which
        # a raw substring check missed.
        rewrite = {"hook": "Tôi không ngờ chồng mình lại làm vậy!",
                   "script": ("Tôi không ngờ, chồng mình lại làm vậy. "
                              "Chuyện bắt đầu từ một buổi tối..."),
                   "vn_commentary": "Bình luận dài về góc nhìn Việt."}
        narration = main_drama.build_narration(rewrite)
        self.assertEqual(narration.lower().count("không ngờ"), 1)
        self.assertTrue(narration.startswith("Tôi không ngờ, chồng mình"))

    def test_hook_paraphrased_at_script_start_not_duplicated(self):
        # Fuzzy prefix match: a few words shifted, still the same spoken line.
        rewrite = {"hook": "Tôi không ngờ chồng mình lại đối xử như vậy",
                   "script": ("Không ngờ chồng mình lại đối xử như vậy. "
                              "Hôm đó tôi đi làm về sớm hơn thường lệ...")}
        narration = main_drama.build_narration(rewrite)
        self.assertTrue(narration.startswith("Không ngờ chồng mình"))

    def test_exact_repeat_mid_script_not_duplicated(self):
        # Normalized containment scans the whole script, not just its opening —
        # a hook restated verbatim mid-story is still a spoken duplicate.
        rewrite = {"hook": "Mẹ chồng tôi đã nói dối suốt ba năm",
                   "script": ("Chuyện bắt đầu từ ngày cưới. "
                              "Sau này tôi mới biết mẹ chồng tôi đã nói dối "
                              "suốt ba năm, nhưng lúc đó tôi không hề hay.")}
        narration = main_drama.build_narration(rewrite)
        self.assertEqual(narration.lower().count("nói dối suốt ba năm"), 1)

    def test_unrelated_short_hook_kept(self):
        # A genuinely distinct hook must never be fuzzy-matched away.
        rewrite = {"hook": "Cả nhà tôi sốc nặng!",
                   "script": "Hôm đó là một ngày bình thường như mọi ngày khác."}
        narration = main_drama.build_narration(rewrite)
        self.assertTrue(narration.startswith("Cả nhà tôi sốc nặng!"))


class TestNarrationCta(unittest.TestCase):
    """Owner request 07/2026: drama narration ends with a follow-style CTA."""

    CTA = "Follow để nghe chuyện đời mỗi ngày nhé!"

    def test_cta_appended_when_missing(self):
        rewrite = {"hook": "Hook!", "script": "Câu chuyện.",
                   "vn_commentary": "Góc nhìn của mình là vậy đó."}
        with patch.object(main_drama.config, "DRAMA_SUBSCRIBE_CTA", self.CTA):
            narration = main_drama.build_narration(rewrite)
        self.assertTrue(narration.endswith(self.CTA))

    def test_cta_not_duplicated_when_commentary_already_follows(self):
        # The drama CTA is follow-flavored — a commentary that already ends
        # with a "follow" call must not get a second CTA appended.
        rewrite = {"hook": "Hook!", "script": "Câu chuyện.",
                   "vn_commentary": ("Còn bạn thì sao? Follow để nghe chuyện "
                                     "đời mỗi ngày nhé!")}
        with patch.object(main_drama.config, "DRAMA_SUBSCRIBE_CTA", self.CTA):
            narration = main_drama.build_narration(rewrite)
        self.assertEqual(narration.lower().count("follow"), 1)

    def test_cta_not_duplicated_when_commentary_says_subscribe(self):
        # Old stories rewritten before the prompt change may say "đăng ký
        # kênh" — that still counts as a CTA.
        rewrite = {"hook": "Hook!", "script": "Câu chuyện.",
                   "vn_commentary": ("Còn bạn thì sao? Đăng ký kênh để nghe "
                                     "chuyện mới mỗi ngày nhé!")}
        with patch.object(main_drama.config, "DRAMA_SUBSCRIBE_CTA", self.CTA):
            narration = main_drama.build_narration(rewrite)
        self.assertNotIn("Follow để nghe", narration)

    def test_empty_rewrite_gets_no_cta(self):
        with patch.object(main_drama.config, "DRAMA_SUBSCRIBE_CTA", self.CTA):
            self.assertEqual(main_drama.build_narration({}), "")


class RenderBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def _make_story(self, rewritten=None, status="approved"):
        from storage.stories import insert_story, update_status
        story_id = insert_story("reddit", f"src_{id(self)}_{status}", "raw")
        rewritten_json = (json.dumps(rewritten, ensure_ascii=False)
                          if isinstance(rewritten, dict) else rewritten)
        update_status(story_id, status, rewritten_content=rewritten_json)
        from storage.stories import get_story
        return get_story(story_id)


class TestRenderGuards(RenderBase):
    def test_malformed_rewrite_flags_needs_review(self):
        story = self._make_story(rewritten="not { json")
        result = main_drama._render_story(story)
        self.assertIsNone(result)
        from storage.stories import get_story
        self.assertEqual(get_story(story["id"])["status"], "needs_review")

    def test_empty_narration_flags_needs_review(self):
        story = self._make_story(rewritten={"title": "t", "script": ""})
        self.assertIsNone(main_drama._render_story(story))
        from storage.stories import get_story
        self.assertEqual(get_story(story["id"])["status"], "needs_review")

    def test_resume_guard_skips_story_with_existing_video(self):
        story = self._make_story(rewritten={"title": "t", "script": "s"})
        db.insert_video(video_type="short", script_text="s", track="drama",
                        story_id=story["id"])
        with patch.object(main_drama, "_render_story") as render:
            created = main_drama.render_approved_stories(limit=5)
        render.assert_not_called()
        self.assertEqual(created, [])

    def test_limit_respected(self):
        for i in range(3):
            from storage.stories import insert_story, update_status
            sid = insert_story("reddit", f"limit_{i}", "raw")
            update_status(sid, "approved",
                          rewritten_content=json.dumps({"title": "t", "script": "s"}))
        with patch.object(main_drama, "_render_story",
                          side_effect=[101, 102, 103]) as render:
            created = main_drama.render_approved_stories(limit=2)
        self.assertEqual(len(created), 2)
        self.assertEqual(render.call_count, 2)


class TestRenderHappyPath(RenderBase):
    def test_full_render_flow(self):
        rewrite = {
            "title": "Mẹ chồng gây sốc",
            "hook": "Không ai ngờ được!",
            "script": "Chuyện là thế này, tôi tên Mai...",
            "vn_commentary": "Ở Việt Nam mình chuyện này...",
            "thumbnail_prompt": "shocked woman",
            "tags": ["mẹ chồng", "drama"],
        }
        story = self._make_story(rewritten=rewrite)

        def fake_tts(text, track, output_path):
            with open(output_path, "wb") as f:
                f.write(b"audio")
            return output_path

        def fake_srt(text, duration, output_path):
            with open(output_path, "w") as f:
                f.write("1\n00:00:00,000 --> 00:00:01,000\nx\n")
            return output_path

        def fake_compose(audio, srt, output_path, **kwargs):
            with open(output_path, "wb") as f:
                f.write(b"video")
            return output_path

        with patch.object(db.config, "VIDEO_OUTPUT_DIR", self.tmp), \
             patch("video.tts_client.synthesize_for_track", side_effect=fake_tts), \
             patch("video.tts_client.get_audio_duration", return_value=60.0), \
             patch("video.subtitle_generator.generate_srt", side_effect=fake_srt), \
             patch("video.drama_composer.compose_drama_video",
                   side_effect=fake_compose) as compose, \
             patch("video.image_generator.generate_illustration", return_value=None), \
             patch("notifier.review_bot.auto_dispatch", return_value=True) as dispatch:
            video_id = main_drama._render_story(story)

        self.assertIsNotNone(video_id)
        video = db.get_video(video_id)
        self.assertEqual(video["story_id"], story["id"])
        self.assertEqual(video["track"], "drama")
        self.assertEqual(video["destination"], "drama_youtube")
        self.assertEqual(video["status"], "ready")
        self.assertTrue(os.path.exists(video["video_path"]))
        # narration chứa cả hook lẫn commentary
        self.assertIn("Không ai ngờ được!", video["script_text"])
        self.assertIn("Ở Việt Nam mình", video["script_text"])
        # hashtags sinh từ tags (bỏ dấu cách)
        self.assertIn("#mẹchồng", video["tiktok_hashtags"])
        # story chuyển 'produced', vn_commentary được truyền cho composer
        from storage.stories import get_story
        self.assertEqual(get_story(story["id"])["status"], "produced")
        self.assertEqual(compose.call_args.kwargs.get("vn_commentary"),
                         rewrite["vn_commentary"])
        dispatch.assert_called_once_with(video_id)

    def test_tts_failure_marks_video_failed_and_story_retryable(self):
        story = self._make_story(rewritten={"title": "t", "script": "nội dung"})
        with patch.object(db.config, "VIDEO_OUTPUT_DIR", self.tmp), \
             patch("video.tts_client.synthesize_for_track", return_value=None):
            self.assertIsNone(main_drama._render_story(story))
        from storage.stories import get_story
        self.assertEqual(get_story(story["id"])["status"], "approved")
        videos = db.get_videos_by_story(story["id"])
        self.assertEqual([v["status"] for v in videos], ["failed"])
        # row 'failed' KHÔNG chặn retry — lần chạy sau story được render lại
        with patch.object(main_drama, "_render_story", return_value=42) as render:
            created = main_drama.render_approved_stories(limit=1)
        render.assert_called_once()
        self.assertEqual(created, [42])


class TestDispatchStuckVideos(RenderBase):
    def test_ready_drama_video_gets_redispatched(self):
        # auto_dispatch từng fail (Telegram/scheduler down) → video kẹt 'ready',
        # story đã 'produced' — render run sau phải tự dispatch lại (Codex PR #70).
        video_id = db.insert_video(video_type="short", script_text="x",
                                   track="drama", destination="drama_youtube")
        db.update_video_status(video_id, "ready")
        with patch("notifier.review_bot.auto_dispatch", return_value=True) as dispatch:
            main_drama.render_approved_stories(limit=0)
        dispatch.assert_called_once_with(video_id)

    def test_ai_ready_videos_not_touched(self):
        video_id = db.insert_video(video_type="short", script_text="x")  # track ai
        db.update_video_status(video_id, "ready")
        with patch("notifier.review_bot.auto_dispatch") as dispatch:
            main_drama.render_approved_stories(limit=0)
        dispatch.assert_not_called()


class TestRunDaily(RenderBase):
    def test_step_errors_collected_not_raised(self):
        with patch.object(main_drama, "render_approved_stories",
                          side_effect=RuntimeError("render boom")), \
             patch.object(main_drama, "_send_summary_safe") as summ:
            summary = main_drama.run_daily(steps=["render"])
        self.assertEqual(summary["errors"], ["render: render boom"])
        summ.assert_called_once()

    def test_only_selected_steps_run(self):
        with patch.object(main_drama, "render_approved_stories",
                          return_value=[]) as render, \
             patch.object(main_drama, "_send_summary_safe"):
            summary = main_drama.run_daily(steps=["render"])
        render.assert_called_once()
        self.assertNotIn("collected", summary)
        self.assertNotIn("scored", summary)

    def test_hf_unavailable_is_soft_skip_not_summary_error(self):
        # PA1 (issue #90): a "dataset viewer unavailable" HF failure must NOT
        # spam the daily summary — it's a soft condition covered by the backfill.
        import collectors.hf_drama_importer as hf
        with patch.object(main_drama.config, "HF_DRAMA_DAILY_ENABLED", True), \
             patch.object(main_drama.config, "HF_DRAMA_DAILY_MODE", "cursor"), \
             patch("collectors.reddit_drama_collector.collect_all_drama", return_value=0), \
             patch("collectors.lemmy_drama_collector.collect_all_lemmy", return_value=0), \
             patch("collectors.hf_drama_importer.import_daily",
                   side_effect=hf.HFDatasetUnavailableError("viewer down")), \
             patch.object(main_drama, "_send_summary_safe"):
            summary = main_drama.run_daily(steps=["collect"])
        self.assertEqual(summary["collected"], 0)
        self.assertEqual(summary["errors"], [])  # soft-skipped, not surfaced

    def test_hf_hard_error_is_surfaced_in_summary(self):
        import collectors.hf_drama_importer as hf
        with patch.object(main_drama.config, "HF_DRAMA_DAILY_ENABLED", True), \
             patch.object(main_drama.config, "HF_DRAMA_DAILY_MODE", "cursor"), \
             patch("collectors.reddit_drama_collector.collect_all_drama", return_value=0), \
             patch("collectors.lemmy_drama_collector.collect_all_lemmy", return_value=0), \
             patch("collectors.hf_drama_importer.import_daily",
                   side_effect=hf.HFImportError("real bug")), \
             patch.object(main_drama, "_send_summary_safe"):
            summary = main_drama.run_daily(steps=["collect"])
        self.assertTrue(any("collect[hf]" in e for e in summary["errors"]))


if __name__ == "__main__":
    unittest.main()
