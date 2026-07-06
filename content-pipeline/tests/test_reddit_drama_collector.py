"""Tests for collectors/reddit_drama_collector.py (Phase 2 — Drama Source Layer)."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import collectors.reddit_drama_collector as drama_collector
import storage.database as db
import storage.migrate as migrate
import storage.stories as stories

FIXTURE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>reddit: the front page of the internet</title>
  <entry>
    <id>t3_abc111</id>
    <link href="https://www.reddit.com/r/AskReddit/comments/abc111/some_title_here/"/>
    <title>AITA for refusing to pay for the wedding?</title>
    <summary>Some summary text here.</summary>
  </entry>
  <entry>
    <id>t3_abc222</id>
    <link href="https://www.reddit.com/r/AskReddit/comments/abc222/another_post/"/>
    <title>My coworker took credit for my work</title>
    <summary>Another summary.</summary>
  </entry>
  <entry>
    <title>Entry with no resolvable post id</title>
    <link href="https://www.reddit.com/r/AskReddit/weird_url_format/"/>
  </entry>
</feed>
"""


def _detail_json(selftext="Full story body.", ups=6000, over_18=False):
    return [
        {
            "data": {
                "children": [
                    {"data": {"selftext": selftext, "score": ups, "over_18": over_18}}
                ]
            }
        },
        {"data": {"children": []}},
    ]


class TestExtractPostId(unittest.TestCase):
    def test_extracts_from_comments_link(self):
        entry = {"link": "https://www.reddit.com/r/AskReddit/comments/abc123/title_slug/"}
        self.assertEqual(drama_collector._extract_post_id(entry), "abc123")

    def test_extracts_from_fullname_id(self):
        entry = {"id": "t3_xyz789", "link": ""}
        self.assertEqual(drama_collector._extract_post_id(entry), "xyz789")

    def test_returns_none_when_unresolvable(self):
        entry = {"link": "https://www.reddit.com/r/AskReddit/weird/", "id": "not-a-fullname"}
        self.assertIsNone(drama_collector._extract_post_id(entry))


class TestParseRssEntries(unittest.TestCase):
    def test_parses_valid_entries(self):
        entries = drama_collector.parse_rss_entries(FIXTURE_RSS)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["post_id"], "abc111")
        self.assertEqual(entries[1]["post_id"], "abc222")

    def test_skips_entries_without_resolvable_post_id(self):
        entries = drama_collector.parse_rss_entries(FIXTURE_RSS)
        titles = [e["title"] for e in entries]
        self.assertNotIn("Entry with no resolvable post id", titles)

    def test_title_and_link_captured(self):
        entries = drama_collector.parse_rss_entries(FIXTURE_RSS)
        self.assertEqual(entries[0]["title"], "AITA for refusing to pay for the wedding?")
        self.assertIn("abc111", entries[0]["link"])


class TestParsePostDetail(unittest.TestCase):
    def test_extracts_fields(self):
        detail = drama_collector.parse_post_detail(_detail_json(selftext="Body", ups=7000))
        self.assertEqual(detail["selftext"], "Body")
        self.assertEqual(detail["ups"], 7000)
        self.assertFalse(detail["over_18"])

    def test_over_18_flag(self):
        detail = drama_collector.parse_post_detail(_detail_json(over_18=True))
        self.assertTrue(detail["over_18"])

    def test_malformed_response_returns_none(self):
        self.assertIsNone(drama_collector.parse_post_detail({"unexpected": "shape"}))

    def test_empty_list_returns_none(self):
        self.assertIsNone(drama_collector.parse_post_detail([]))


class TestRateLimiter(unittest.TestCase):
    def test_second_call_waits_min_interval(self):
        limiter = drama_collector._RateLimiter(min_interval=0.05)
        start = time.monotonic()
        limiter.wait()
        limiter.wait()
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.04)  # small tolerance for scheduler jitter


class TestCollectSubreddit(unittest.TestCase):
    """Integration-ish test: mocks the two network calls, exercises the real
    dedupe + insert path against a temp DB."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

        self._rss_patch = patch.object(
            drama_collector, "fetch_subreddit_rss",
            return_value=[
                {"post_id": "aaa", "title": "Story A", "link": "https://x/aaa", "summary": ""},
                {"post_id": "bbb", "title": "Story B", "link": "https://x/bbb", "summary": ""},
                {"post_id": "ccc", "title": "Story C (nsfw)", "link": "https://x/ccc", "summary": ""},
                {"post_id": "ddd", "title": "Story D (low score)", "link": "https://x/ddd", "summary": ""},
            ],
        )
        self._rss_patch.start()

        def fake_detail(subreddit, post_id):
            # Return the RAW reddit JSON shape (list of 2 listings), same as
            # the real _fetch_post_json — exercises the real parse_post_detail()
            # call inside collect_subreddit() instead of bypassing it.
            raw = {
                "aaa": _detail_json(selftext="Body A", ups=6000, over_18=False),
                "bbb": _detail_json(selftext="Body B", ups=8000, over_18=False),
                "ccc": _detail_json(selftext="Body C", ups=9000, over_18=True),
                "ddd": _detail_json(selftext="Body D", ups=10, over_18=False),
            }[post_id]
            return raw

        self._detail_patch = patch.object(drama_collector, "_fetch_post_json", side_effect=fake_detail)
        self._detail_patch.start()

    def tearDown(self):
        self._detail_patch.stop()
        self._rss_patch.stop()
        self._patch.stop()

    def test_inserts_only_eligible_posts(self):
        count = drama_collector.collect_subreddit(
            {"name": "AskReddit", "min_upvotes": 5000, "weight": 1.0}
        )
        self.assertEqual(count, 2)  # aaa + bbb; ccc is nsfw, ddd is below threshold
        pending = stories.get_pending(track="drama")
        source_ids = {s["source_id"] for s in pending}
        self.assertEqual(source_ids, {"reddit_aaa", "reddit_bbb"})

    def test_metadata_stored(self):
        drama_collector.collect_subreddit(
            {"name": "AskReddit", "min_upvotes": 5000, "weight": 1.2}
        )
        pending = stories.get_pending(track="drama")
        story = next(s for s in pending if s["source_id"] == "reddit_aaa")
        self.assertEqual(story["metadata"]["subreddit"], "AskReddit")
        self.assertEqual(story["metadata"]["upvotes"], 6000)
        self.assertEqual(story["metadata"]["weight"], 1.2)

    def test_second_run_skips_duplicates(self):
        first = drama_collector.collect_subreddit(
            {"name": "AskReddit", "min_upvotes": 5000, "weight": 1.0}
        )
        second = drama_collector.collect_subreddit(
            {"name": "AskReddit", "min_upvotes": 5000, "weight": 1.0}
        )
        self.assertEqual(first, 2)
        self.assertEqual(second, 0)

    def test_fetch_post_json_raw_shape_is_actually_parsed(self):
        # Regression test: _fetch_post_json returns Reddit's RAW JSON shape
        # (a list of 2 listings), not a pre-parsed dict. collect_subreddit()
        # must run it through parse_post_detail() itself — a prior version
        # skipped that step and crashed with "list indices must be integers"
        # on any real (non-mocked-as-a-dict) response.
        with patch.object(
            drama_collector, "_fetch_post_json",
            return_value=_detail_json(selftext="Real body", ups=6000, over_18=False),
        ):
            count = drama_collector.collect_subreddit(
                {"name": "AskReddit", "min_upvotes": 5000, "weight": 1.0}
            )
        self.assertGreater(count, 0)


class TestCollectSubredditRemovedPosts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

        self._rss_patch = patch.object(
            drama_collector, "fetch_subreddit_rss",
            return_value=[
                {"post_id": "removed1", "title": "Removed post", "link": "https://x/r1", "summary": ""},
                {"post_id": "deleted1", "title": "Deleted post", "link": "https://x/d1", "summary": ""},
                {"post_id": "ok1", "title": "Fine post", "link": "https://x/ok1", "summary": ""},
            ],
        )
        self._rss_patch.start()

        def fake_detail(subreddit, post_id):
            selftext = {
                "removed1": "[removed]",
                "deleted1": "[deleted]",
                "ok1": "A real story body",
            }[post_id]
            return _detail_json(selftext=selftext, ups=6000, over_18=False)

        self._detail_patch = patch.object(drama_collector, "_fetch_post_json", side_effect=fake_detail)
        self._detail_patch.start()

    def tearDown(self):
        self._detail_patch.stop()
        self._rss_patch.stop()
        self._patch.stop()

    def test_removed_and_deleted_bodies_are_skipped(self):
        count = drama_collector.collect_subreddit(
            {"name": "AskReddit", "min_upvotes": 5000, "weight": 1.0}
        )
        self.assertEqual(count, 1)
        pending = stories.get_pending(track="drama")
        self.assertEqual({s["source_id"] for s in pending}, {"reddit_ok1"})


class TestCollectAllDrama(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def test_continues_after_one_subreddit_errors(self):
        def fake_collect(sub_config):
            if sub_config["name"] == "AmItheAsshole":
                raise RuntimeError("network blew up")
            return 1

        with patch.object(drama_collector, "collect_subreddit", side_effect=fake_collect):
            total = drama_collector.collect_all_drama()
        # 4 of 5 subreddits succeed with 1 each; AmItheAsshole raises and is skipped.
        self.assertEqual(total, 4)

    def test_raises_when_every_subreddit_fails(self):
        # Regression test: a total outage (every subreddit call raises) must
        # NOT look like a quiet "0 new stories" success — the caller (see
        # __main__) only records collector_health success after this
        # function returns normally, so this has to actually raise.
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
            total = drama_collector.collect_all_drama()  # must not raise
        self.assertEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
