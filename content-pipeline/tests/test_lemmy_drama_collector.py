"""Tests for collectors/lemmy_drama_collector.py (issue #78 follow-up)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import collectors.lemmy_drama_collector as lemmy
import storage.database as db
import storage.migrate as migrate
import storage.stories as stories


def _post(pid, title="Title", body="A real story body.", score=50,
          nsfw=False, stickied=False, removed=False, ap_id=None):
    return {
        "post": {
            "id": pid,
            "name": title,
            "body": body,
            "ap_id": ap_id if ap_id is not None else f"https://lemmy.world/post/{pid}",
            "nsfw": nsfw,
            "featured_community": stickied,
            "featured_local": False,
            "removed": removed,
            "deleted": False,
        },
        "counts": {"score": score},
    }


def _listing(*posts):
    return {"posts": list(posts)}


class TestParseListing(unittest.TestCase):
    def test_parses_fields(self):
        posts = lemmy.parse_listing(_listing(_post(1, title="AITA?", body="Body", score=42)))
        self.assertEqual(len(posts), 1)
        p = posts[0]
        self.assertEqual(p["title"], "AITA?")
        self.assertEqual(p["body"], "Body")
        self.assertEqual(p["score"], 42)
        self.assertFalse(p["nsfw"])
        self.assertTrue(p["source_id"].startswith("lemmy_"))

    def test_malformed_returns_empty(self):
        self.assertEqual(lemmy.parse_listing({"unexpected": 1}), [])
        self.assertEqual(lemmy.parse_listing(None), [])
        self.assertEqual(lemmy.parse_listing({"posts": "nope"}), [])

    def test_stickied_and_nsfw_flags(self):
        posts = lemmy.parse_listing(_listing(
            _post(1, nsfw=True), _post(2, stickied=True),
        ))
        by = {p["id"]: p for p in posts}
        self.assertTrue(by[1]["nsfw"])
        self.assertTrue(by[2]["stickied"])

    def test_source_id_stable_from_ap_id(self):
        a = lemmy.parse_listing(_listing(_post(1, ap_id="https://x/post/1")))[0]
        b = lemmy.parse_listing(_listing(_post(999, ap_id="https://x/post/1")))[0]
        self.assertEqual(a["source_id"], b["source_id"])  # same ap_id → same id


class _LemmyDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestCollectCommunity(_LemmyDBTest):
    def _run(self, posts, min_score=10):
        with patch.object(lemmy.config, "LEMMY_MIN_SCORE", min_score), \
             patch.object(lemmy, "fetch_community_top",
                          return_value=lemmy.parse_listing(_listing(*posts))):
            return lemmy.collect_community("relationship_advice@lemmy.world")

    def test_inserts_only_eligible(self):
        count = self._run([
            _post(1, score=50),                       # ok
            _post(2, score=5),                        # below min
            _post(3, score=50, nsfw=True),            # nsfw
            _post(4, score=50, stickied=True),        # pinned
            _post(5, score=50, body="[removed]"),     # removed body
            _post(6, score=50, body="   "),           # empty body
        ], min_score=10)
        self.assertEqual(count, 1)
        pending = stories.get_pending(track="drama")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source"], "lemmy")

    def test_metadata_and_dedupe(self):
        posts = [_post(1, score=77)]
        first = self._run(posts)
        second = self._run(posts)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # deduped by source_id
        story = stories.get_pending(track="drama")[0]
        self.assertEqual(story["metadata"]["score"], 77)
        self.assertEqual(story["metadata"]["community"], "relationship_advice@lemmy.world")


class TestFetchCommunityTop(_LemmyDBTest):
    def test_none_raises_fetch_error(self):
        with patch.object(lemmy, "_fetch_community", return_value=None):
            with self.assertRaises(lemmy.LemmyFetchError):
                lemmy.fetch_community_top("x@y")

    def test_empty_listing_returns_empty(self):
        with patch.object(lemmy, "_fetch_community", return_value={"posts": []}):
            self.assertEqual(lemmy.fetch_community_top("x@y"), [])


class TestCollectAllLemmy(_LemmyDBTest):
    def test_disabled_returns_zero(self):
        with patch.object(lemmy.config, "LEMMY_ENABLED", False):
            with patch.object(lemmy, "collect_community") as cc:
                self.assertEqual(lemmy.collect_all_lemmy(), 0)
            cc.assert_not_called()

    def test_partial_failure_does_not_raise(self):
        with patch.object(lemmy.config, "LEMMY_ENABLED", True), \
             patch.object(lemmy.config, "LEMMY_COMMUNITIES", ["a@x", "b@x"]):
            def fake(community):
                if community == "a@x":
                    raise lemmy.LemmyFetchError("down")
                return 2
            with patch.object(lemmy, "collect_community", side_effect=fake):
                self.assertEqual(lemmy.collect_all_lemmy(), 2)

    def test_total_failure_raises(self):
        with patch.object(lemmy.config, "LEMMY_ENABLED", True), \
             patch.object(lemmy.config, "LEMMY_COMMUNITIES", ["a@x", "b@x"]), \
             patch.object(lemmy, "collect_community",
                          side_effect=lemmy.LemmyFetchError("down")):
            with self.assertRaises(RuntimeError):
                lemmy.collect_all_lemmy()


if __name__ == "__main__":
    unittest.main()
