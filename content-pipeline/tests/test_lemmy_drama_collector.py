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


def _comments(*specs):
    """Build a /comment/list response from (content, score) specs."""
    return {"comments": [
        {"comment": {"content": c, "removed": s.get("removed", False),
                     "deleted": False},
         "counts": {"score": s.get("score", 20)}}
        for c, s in specs
    ]}


class TestParseComments(unittest.TestCase):
    def test_parses_fields(self):
        out = lemmy.parse_comments(_comments(("An answer", {"score": 42})))
        self.assertEqual(out[0]["content"], "An answer")
        self.assertEqual(out[0]["score"], 42)
        self.assertFalse(out[0]["removed"])

    def test_malformed_returns_empty(self):
        self.assertEqual(lemmy.parse_comments({"x": 1}), [])
        self.assertEqual(lemmy.parse_comments(None), [])


class TestFetchTopComments(unittest.TestCase):
    def test_filters_and_caps(self):
        with patch.object(lemmy.config, "LEMMY_QA_TOP_COMMENTS", 2), \
             patch.object(lemmy.config, "LEMMY_QA_MIN_COMMENT_SCORE", 5), \
             patch.object(lemmy.config, "LEMMY_QA_MIN_COMMENT_CHARS", 10), \
             patch.object(lemmy, "_fetch_comments", return_value=_comments(
                 ("This is a good long answer number one", {"score": 90}),
                 ("short", {"score": 90}),                       # too short
                 ("Another sufficiently long answer here", {"score": 2}),   # low score
                 ("A removed one that is long enough", {"score": 90, "removed": True}),
                 ("Second good answer that is long enough", {"score": 50}),
                 ("Third good answer but capped out here", {"score": 40}),
             )):
            answers = lemmy.fetch_top_comments(123)
        self.assertEqual(answers, [
            "This is a good long answer number one",
            "Second good answer that is long enough",
        ])  # top 2 that pass filters, best-first

    def test_fetch_failure_returns_none(self):
        with patch.object(lemmy, "_fetch_comments", return_value=None):
            self.assertIsNone(lemmy.fetch_top_comments(123))


class TestBuildQaContent(unittest.TestCase):
    def test_joins_question_body_answers(self):
        out = lemmy._build_qa_content("The question?", "extra body", ["a1", "a2"])
        self.assertEqual(out, "The question?\n\nextra body\n\na1\n\na2")

    def test_skips_empty_body(self):
        out = lemmy._build_qa_content("Q?", "  ", ["a1"])
        self.assertEqual(out, "Q?\n\na1")


class TestCollectQa(_LemmyDBTest):
    def _run(self, posts, answers_by_id):
        with patch.object(lemmy.config, "LEMMY_QA_COMMUNITIES", ["asklemmy@lemmy.world"]), \
             patch.object(lemmy.config, "LEMMY_QA_MIN_COMMENTS", 2), \
             patch.object(lemmy.config, "LEMMY_MIN_SCORE", 10), \
             patch.object(lemmy, "fetch_community_top",
                          return_value=lemmy.parse_listing(_listing(*posts))), \
             patch.object(lemmy, "fetch_top_comments",
                          side_effect=lambda pid: answers_by_id.get(pid)):
            return lemmy.collect_community("asklemmy@lemmy.world")

    def test_assembles_qa_story(self):
        # Question post (empty body) with enough answers → a Q&A story.
        count = self._run(
            [_post(1, title="Scariest thing?", body="", score=500)],
            {1: ["Answer one is here", "Answer two is here"]},
        )
        self.assertEqual(count, 1)
        story = stories.get_pending(track="drama")[0]
        self.assertEqual(story["title"], "Scariest thing?")
        self.assertEqual(story["metadata"]["format"], "qa")
        self.assertEqual(story["metadata"]["num_answers"], 2)
        self.assertIn("Answer one is here", story["raw_content"])

    def test_skips_when_too_few_answers(self):
        count = self._run(
            [_post(1, title="Q?", body="", score=500)],
            {1: ["only one answer"]},   # < LEMMY_QA_MIN_COMMENTS (2)
        )
        self.assertEqual(count, 0)

    def test_skips_when_comment_fetch_fails(self):
        count = self._run(
            [_post(1, title="Q?", body="", score=500)],
            {1: None},   # comment fetch returned None
        )
        self.assertEqual(count, 0)


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
