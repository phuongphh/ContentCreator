"""Tests for drama_youtube publish notification format tag (issue #130)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.stories as stories
import notifier.telegram_bot as tb
import notifier.publish_format as pf
import scheduler.post_scheduler as ps


class TestLabels(unittest.TestCase):
    def test_tag_on_first_line_link_unchanged(self):
        for fmt in ("normal", "conversation", "debate"):
            with patch.object(tb, "_send_text", return_value=True) as s:
                tb.send_publish_notification(7, "Kênh", "http://u", narrative_format=fmt)
            self.assertEqual(
                s.call_args.args[0],
                f"🚀 Video 7 [{fmt}] đã đăng lên Kênh!\n🔗 http://u")

    def test_default_call_unchanged(self):
        with patch.object(tb, "_send_text", return_value=True) as s:
            tb.send_publish_notification(7, "Kênh", "http://u")
        self.assertEqual(s.call_args.args[0], "🚀 Video 7 đã đăng lên Kênh!\n🔗 http://u")

    def test_normalize(self):
        self.assertEqual(pf.normalize_format("narrative"), "normal")
        self.assertEqual(pf.normalize_format("weird"), "normal")
        self.assertEqual(pf.normalize_format(None), "normal")
        self.assertEqual(pf.normalize_format("Debate"), "debate")


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._patch = patch.object(db.config, "DB_PATH", os.path.join(self.tmp, "t.db"))
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def _story(self, n, content):
        sid = stories.insert_story("test", f"s{n}", "x" * 40 + str(n))
        stories.update_status(sid, "produced", rewritten_content=content)
        return sid

    def _notify(self, story_id, channel="drama_youtube"):
        vid = db.insert_video("short", "x", track="drama", destination=channel,
                              story_id=story_id)
        post = {"video_id": vid, "channel_key": channel}
        with patch("notifier.telegram_bot._send_text", return_value=True) as s:
            ps._notify_published_safe(post, "http://u")
        return s.call_args.args[0]

    def test_conversation_and_debate(self):
        for fmt in ("conversation", "debate"):
            sid = self._story(fmt, json.dumps({"narrative_format": fmt}))
            first = self._notify(sid).split("\n")[0]
            self.assertIn(f"[{fmt}]", first)

    def test_narrative_maps_to_normal(self):
        sid = self._story("n", json.dumps({"narrative_format": "narrative"}))
        self.assertIn("[normal]", self._notify(sid))

    def test_invalid_missing_defaults_normal(self):
        bad = self._story("b", "not json{")
        unk = self._story("u", json.dumps({"narrative_format": "zzz"}))
        for sid in (bad, unk, None, 99999):
            msg = self._notify(sid)
            self.assertIn("[normal]", msg.split("\n")[0])
            self.assertTrue(msg.endswith("🔗 http://u"))

    def test_ai_youtube_has_no_tag(self):
        sid = self._story("c", json.dumps({"narrative_format": "debate"}))
        msg = self._notify(sid, channel="ai_youtube")
        self.assertRegex(msg, r"\A🚀 Video \d+ đã đăng lên \[2P\] AI Hôm Nay \(ai_youtube\)!\n🔗 http://u\Z")

    def test_failure_still_logged(self):
        post = {"video_id": 1, "channel_key": "drama_youtube"}
        with patch("notifier.telegram_bot._send_text", return_value=False), \
             self.assertLogs(ps.logger, "WARNING"):
            ps._notify_published_safe(post, "http://u")


if __name__ == "__main__":
    unittest.main()
