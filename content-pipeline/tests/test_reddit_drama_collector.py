"""Tests for collectors/reddit_drama_collector.py (Phase 2 + issue #78 rework)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import collectors.reddit_drama_collector as drama_collector
import storage.database as db
import storage.migrate as migrate
import storage.stories as stories


def _listing(*posts) -> dict:
    """Build a Reddit /top listing JSON body from (post_id, **overrides) specs."""
    children = []
    for spec in posts:
        data = {
            "id": spec["post_id"],
            "title": spec.get("title", f"Title {spec['post_id']}"),
            "permalink": spec.get("permalink", f"/r/AskReddit/comments/{spec['post_id']}/slug/"),
            "selftext": spec.get("selftext", "A real story body long enough."),
            "score": spec.get("score", 6000),
            "over_18": spec.get("over_18", False),
            "stickied": spec.get("stickied", False),
        }
        children.append({"kind": "t3", "data": data})
    return {"kind": "Listing", "data": {"children": children}}


class TestPermalinkUrl(unittest.TestCase):
    def test_relative_path_becomes_absolute(self):
        self.assertEqual(
            drama_collector._permalink_url("/r/x/comments/abc/slug/"),
            "https://www.reddit.com/r/x/comments/abc/slug/",
        )

    def test_absolute_url_passthrough(self):
        self.assertEqual(
            drama_collector._permalink_url("https://redd.it/abc"), "https://redd.it/abc"
        )

    def test_empty_returns_empty(self):
        self.assertEqual(drama_collector._permalink_url(""), "")


class TestParseListing(unittest.TestCase):
    def test_parses_all_fields(self):
        raw = _listing({"post_id": "abc111", "title": "AITA?", "selftext": "Body", "score": 7000})
        posts = drama_collector.parse_listing(raw)
        self.assertEqual(len(posts), 1)
        p = posts[0]
        self.assertEqual(p["post_id"], "abc111")
        self.assertEqual(p["title"], "AITA?")
        self.assertEqual(p["selftext"], "Body")
        self.assertEqual(p["ups"], 7000)
        self.assertFalse(p["over_18"])
        self.assertIn("abc111", p["link"])

    def test_skips_children_without_id(self):
        raw = {"data": {"children": [{"data": {"title": "no id"}}]}}
        self.assertEqual(drama_collector.parse_listing(raw), [])

    def test_malformed_shape_returns_empty(self):
        self.assertEqual(drama_collector.parse_listing({"unexpected": True}), [])
        self.assertEqual(drama_collector.parse_listing([]), [])
        self.assertEqual(drama_collector.parse_listing(None), [])

    def test_over_18_and_stickied_flags(self):
        raw = _listing(
            {"post_id": "nsfw1", "over_18": True},
            {"post_id": "pin1", "stickied": True},
        )
        posts = drama_collector.parse_listing(raw)
        by_id = {p["post_id"]: p for p in posts}
        self.assertTrue(by_id["nsfw1"]["over_18"])
        self.assertTrue(by_id["pin1"]["stickied"])


class _DramaDBTest(unittest.TestCase):
    """Base: fresh temp DB per test with migrations applied."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestCollectSubreddit(_DramaDBTest):
    def _run(self, posts, min_upvotes=5000, weight=1.0):
        with patch.object(drama_collector, "fetch_subreddit_top", return_value=posts):
            return drama_collector.collect_subreddit(
                {"name": "AskReddit", "min_upvotes": min_upvotes, "weight": weight}
            )

    def test_inserts_only_eligible_posts(self):
        posts = drama_collector.parse_listing(_listing(
            {"post_id": "aaa", "score": 6000},
            {"post_id": "bbb", "score": 8000},
            {"post_id": "ccc", "score": 9000, "over_18": True},   # nsfw
            {"post_id": "ddd", "score": 10},                       # below threshold
            {"post_id": "eee", "score": 9000, "stickied": True},   # pinned
            {"post_id": "fff", "score": 9000, "selftext": "[removed]"},
        ))
        count = self._run(posts)
        self.assertEqual(count, 2)  # aaa + bbb only
        pending = stories.get_pending(track="drama")
        self.assertEqual({s["source_id"] for s in pending}, {"reddit_aaa", "reddit_bbb"})

    def test_metadata_stored(self):
        posts = drama_collector.parse_listing(_listing({"post_id": "aaa", "score": 6000}))
        self._run(posts, weight=1.2)
        story = stories.get_pending(track="drama")[0]
        self.assertEqual(story["metadata"]["subreddit"], "AskReddit")
        self.assertEqual(story["metadata"]["upvotes"], 6000)
        self.assertEqual(story["metadata"]["weight"], 1.2)
        self.assertIn("aaa", story["metadata"]["url"])

    def test_second_run_skips_duplicates(self):
        posts = drama_collector.parse_listing(_listing(
            {"post_id": "aaa", "score": 6000}, {"post_id": "bbb", "score": 8000},
        ))
        first = self._run(posts)
        second = self._run(posts)
        self.assertEqual(first, 2)
        self.assertEqual(second, 0)

    def test_removed_and_deleted_bodies_skipped(self):
        posts = drama_collector.parse_listing(_listing(
            {"post_id": "r1", "score": 6000, "selftext": "[removed]"},
            {"post_id": "d1", "score": 6000, "selftext": "[deleted]"},
            {"post_id": "ok1", "score": 6000, "selftext": "Real body"},
        ))
        count = self._run(posts)
        self.assertEqual(count, 1)
        self.assertEqual(
            {s["source_id"] for s in stories.get_pending(track="drama")}, {"reddit_ok1"}
        )

    def test_end_to_end_through_client(self):
        # Exercise the real fetch_subreddit_top → reddit_client.get_json path,
        # mocking only the network boundary.
        raw = _listing({"post_id": "zzz", "score": 6000, "selftext": "Body"})
        with patch.object(drama_collector.reddit_client, "get_json", return_value=raw):
            count = drama_collector.collect_subreddit(
                {"name": "AskReddit", "min_upvotes": 5000, "weight": 1.0}
            )
        self.assertEqual(count, 1)
        self.assertEqual(
            {s["source_id"] for s in stories.get_pending(track="drama")}, {"reddit_zzz"}
        )

    def test_blocked_source_returns_zero_not_crash(self):
        # get_json returns None on a 403 block — collector must treat it as
        # "0 stories", not raise.
        with patch.object(drama_collector.reddit_client, "get_json", return_value=None):
            count = drama_collector.collect_subreddit(
                {"name": "AskReddit", "min_upvotes": 5000, "weight": 1.0}
            )
        self.assertEqual(count, 0)


class TestCollectAllDrama(_DramaDBTest):
    def test_continues_after_one_subreddit_errors(self):
        def fake_collect(sub_config):
            if sub_config["name"] == "AmItheAsshole":
                raise RuntimeError("network blew up")
            return 1

        with patch.object(drama_collector, "collect_subreddit", side_effect=fake_collect):
            total = drama_collector.collect_all_drama()
        self.assertEqual(total, 4)

    def test_raises_when_every_subreddit_fails(self):
        with patch.object(
            drama_collector, "collect_subreddit",
            side_effect=RuntimeError("network blew up"),
        ):
            with self.assertRaises(RuntimeError):
                drama_collector.collect_all_drama()

    def test_partial_failure_does_not_raise(self):
        def fake_collect(sub_config):
            if sub_config["name"] == "AmItheAsshole":
                raise RuntimeError("network blew up")
            return 0

        with patch.object(drama_collector, "collect_subreddit", side_effect=fake_collect):
            total = drama_collector.collect_all_drama()
        self.assertEqual(total, 0)

    def test_all_blocked_returns_zero_without_raising(self):
        # A 403 block makes every collect_subreddit return 0 (not raise), so
        # collect_all_drama returns 0 and the staleness alert catches the outage
        # — it must NOT raise here (raise is reserved for exceptions, not blocks).
        with patch.object(drama_collector.reddit_client, "get_json", return_value=None):
            total = drama_collector.collect_all_drama()
        self.assertEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
