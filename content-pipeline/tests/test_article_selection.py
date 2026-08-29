"""Tests cho tầng chọn bài phân tích sâu (issue #117 — head-of-line blocking).

Bối cảnh: pipeline 28-29/08 ra 0 video vì 10/10 slot phân tích sâu bị chiếm bởi
bài CŨ không có nội dung — `ai_analyzer` bỏ qua chúng nhưng không có gì đưa
chúng ra khỏi pool, nên hôm sau chúng lại được chọn.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db


def _ts(days_ago: float) -> str:
    """Timestamp kiểu SQLite, lùi `days_ago` ngày (UTC — như CURRENT_TIMESTAMP)."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.strftime("%Y-%m-%d %H:%M:%S")


class ArticleDBTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._patch = patch.object(db.config, "DB_PATH",
                                   os.path.join(self.tmp, "test.db"))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        db.init_db()

    def _add(self, url: str, *, title="Tin AI mới", raw_content=None,
             summary=None, score=None, analysis=None, status="pending",
             days_ago=0.0, source="feed") -> int:
        conn = db.get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO articles (source, title, url, raw_content, summary, "
                "ai_score, ai_analysis, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source, title, url, raw_content, summary, score, analysis,
                 status, _ts(days_ago)),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _status(self, article_id: int) -> str:
        conn = db.get_connection()
        try:
            return conn.execute("SELECT status FROM articles WHERE id = ?",
                                (article_id,)).fetchone()["status"]
        finally:
            conn.close()


class TestUsableContentPredicate(ArticleDBTestBase):
    """Predicate Python và mảnh SQL phải cho CÙNG kết luận (không được trôi lệch)."""

    CASES = [
        (None, None),
        ("", ""),
        ("   ", "\n\t "),
        ("ngắn", None),
        (None, "ngắn"),
        ("x" * 29, None),
        ("x" * 30, None),
        (None, "y" * 30),
        ("x" * 10, "y" * 40),                       # summary giàu hơn raw_content
        ("  " + "x" * 30 + "  ", None),             # whitespace không được tính
        ("\n\n" + "x" * 28 + "\t", None),
        ("Tin AI: ChatGPT có tính năng mới cho người đi làm", None),  # unicode
        ("ChatGPT vừa ra mắt tính năng nhớ ngữ cảnh", "tóm tắt"),
    ]

    def test_python_and_sql_agree(self):
        conn = db.get_connection()
        try:
            for i, (raw, summ) in enumerate(self.CASES):
                conn.execute(
                    "INSERT INTO articles (source, title, url, raw_content, summary) "
                    "VALUES ('t', 'title', ?, ?, ?)", (f"http://x/{i}", raw, summ))
            conn.commit()
            sql_ids = {
                r["id"] for r in conn.execute(
                    f"SELECT id FROM articles WHERE {db._usable_content_sql()}")
            }
        finally:
            conn.close()

        for i, (raw, summ) in enumerate(self.CASES):
            with self.subTest(raw=raw, summary=summ):
                self.assertEqual(
                    db.has_usable_content(raw, summ), (i + 1) in sql_ids,
                    "Python predicate và SQL predicate bất đồng",
                )

    def test_threshold_is_configurable(self):
        with patch.object(db.config, "MIN_ARTICLE_CONTENT_CHARS", 5):
            self.assertTrue(db.has_usable_content("abcde", None))
        with patch.object(db.config, "MIN_ARTICLE_CONTENT_CHARS", 500):
            self.assertFalse(db.has_usable_content("abcde", None))

    def test_invalid_threshold_falls_back_to_default(self):
        with patch.object(db.config, "MIN_ARTICLE_CONTENT_CHARS", "không phải số"):
            self.assertEqual(db._min_content_chars(), 30)

    def test_choose_content_prefers_longer_field(self):
        self.assertEqual(db.choose_article_content("ngắn", "dài hơn nhiều"),
                         "dài hơn nhiều")
        self.assertEqual(db.choose_article_content("nội dung đầy đủ", "tóm"),
                         "nội dung đầy đủ")
        self.assertEqual(db.choose_article_content(None, None), "")
        self.assertEqual(db.choose_article_content("  spaced  ", None), "spaced")


class TestGetArticlesForAnalysis(ArticleDBTestBase):
    BODY = "Nội dung thật của bài viết về AI cho người đi làm." * 2

    def test_content_less_articles_never_selected(self):
        """Regression #117: 10 bài rỗng điểm cao KHÔNG được chiếm slot."""
        for i in range(10):
            self._add(f"http://empty/{i}", score=9.0, days_ago=20,
                      raw_content=None, summary="")
        fresh = [self._add(f"http://fresh/{i}", score=8.0, days_ago=0,
                           raw_content=self.BODY) for i in range(5)]

        picked = db.get_articles_for_analysis(threshold=5.5, limit=10)

        self.assertEqual(sorted(a["id"] for a in picked), sorted(fresh))

    def test_prefers_fresh_article_over_old_one_with_same_score(self):
        old = self._add("http://old", score=9.0, days_ago=10, raw_content=self.BODY)
        new = self._add("http://new", score=9.0, days_ago=0, raw_content=self.BODY)
        picked = db.get_articles_for_analysis(threshold=5.5, limit=1)
        self.assertEqual([a["id"] for a in picked], [new],
                         "bài cũ cùng điểm không được chặn bài mới")
        self.assertNotEqual(old, new)

    def test_old_high_score_loses_to_fresh_lower_score(self):
        self._add("http://old", score=9.5, days_ago=14, raw_content=self.BODY)
        new = self._add("http://new", score=7.0, days_ago=0, raw_content=self.BODY)
        picked = db.get_articles_for_analysis(threshold=5.5, limit=1)
        self.assertEqual([a["id"] for a in picked], [new])

    def test_above_threshold_wins_over_backfill(self):
        low = self._add("http://low", score=4.0, days_ago=0, raw_content=self.BODY)
        high = self._add("http://high", score=6.0, days_ago=3, raw_content=self.BODY)
        picked = db.get_articles_for_analysis(threshold=5.5, limit=1)
        self.assertEqual([a["id"] for a in picked], [high])
        self.assertNotEqual(low, high)

    def test_backfills_below_threshold_when_not_enough(self):
        high = self._add("http://high", score=6.0, raw_content=self.BODY)
        low = self._add("http://low", score=4.0, raw_content=self.BODY)
        picked = db.get_articles_for_analysis(threshold=5.5, limit=5)
        self.assertEqual(sorted(a["id"] for a in picked), sorted([high, low]))

    def test_no_duplicates_between_threshold_and_backfill(self):
        for i in range(3):
            self._add(f"http://a/{i}", score=8.0, raw_content=self.BODY)
        picked = db.get_articles_for_analysis(threshold=5.5, limit=10)
        ids = [a["id"] for a in picked]
        self.assertEqual(len(ids), len(set(ids)))

    def test_skips_already_analyzed_and_non_pending(self):
        self._add("http://done", score=9.0, raw_content=self.BODY,
                  analysis='{"category": "news"}')
        self._add("http://used", score=9.0, raw_content=self.BODY, status="used")
        self._add("http://skipped", score=9.0, raw_content=self.BODY, status="skipped")
        ok = self._add("http://ok", score=6.0, raw_content=self.BODY)
        picked = db.get_articles_for_analysis(threshold=5.5, limit=10)
        self.assertEqual([a["id"] for a in picked], [ok])

    def test_respects_limit(self):
        for i in range(8):
            self._add(f"http://a/{i}", score=8.0, raw_content=self.BODY)
        self.assertEqual(len(db.get_articles_for_analysis(5.5, limit=3)), 3)

    def test_empty_pool_returns_empty_list(self):
        self.assertEqual(db.get_articles_for_analysis(5.5, limit=5), [])


class TestMarkArticlesUnanalyzable(ArticleDBTestBase):
    BODY = "Nội dung thật của bài viết về AI cho người đi làm." * 2

    def test_sweeps_whole_backlog_in_one_call(self):
        empties = [self._add(f"http://empty/{i}", score=9.0, summary="")
                   for i in range(25)]
        keep = self._add("http://ok", score=6.0, raw_content=self.BODY)

        self.assertEqual(db.mark_articles_unanalyzable(), 25)

        for article_id in empties:
            self.assertEqual(self._status(article_id), "skipped")
        self.assertEqual(self._status(keep), "pending")

    def test_does_not_touch_unscored_articles(self):
        """Bài chưa chấm điểm chưa qua rule filter — không phải việc của sweep."""
        fresh = self._add("http://new", score=None, summary="")
        self.assertEqual(db.mark_articles_unanalyzable(), 0)
        self.assertEqual(self._status(fresh), "pending")

    def test_does_not_touch_already_analyzed(self):
        done = self._add("http://done", score=9.0, summary="",
                         analysis='{"category": "news"}')
        self.assertEqual(db.mark_articles_unanalyzable(), 0)
        self.assertEqual(self._status(done), "pending")

    def test_idempotent(self):
        self._add("http://empty", score=9.0, summary="")
        self.assertEqual(db.mark_articles_unanalyzable(), 1)
        self.assertEqual(db.mark_articles_unanalyzable(), 0)

    def test_mark_article_skipped(self):
        article_id = self._add("http://x", score=9.0, raw_content=self.BODY)
        db.mark_article_skipped(article_id)
        self.assertEqual(self._status(article_id), "skipped")


class TestCountAnalysisCandidates(ArticleDBTestBase):
    BODY = "Nội dung thật của bài viết về AI cho người đi làm." * 2

    def test_counts_pending_usable_and_above_threshold(self):
        self._add("http://e1", score=9.0, summary="")
        self._add("http://e2", score=9.0, raw_content="  ")
        self._add("http://u1", score=6.0, raw_content=self.BODY)
        self._add("http://u2", score=4.0, raw_content=self.BODY)
        self._add("http://done", score=9.0, raw_content=self.BODY,
                  analysis='{"category": "news"}')

        stats = db.count_analysis_candidates(threshold=5.5)
        self.assertEqual(stats, {"pending_scored": 4, "usable": 2,
                                 "above_threshold": 1})

    def test_empty_db_returns_zeros(self):
        self.assertEqual(db.count_analysis_candidates(5.5),
                         {"pending_scored": 0, "usable": 0, "above_threshold": 0})

    def test_default_threshold_from_config(self):
        self._add("http://u1", score=6.0, raw_content=self.BODY)
        with patch.object(db.config, "SCORE_THRESHOLD_ANALYSIS", 7.0):
            self.assertEqual(db.count_analysis_candidates()["above_threshold"], 0)
        with patch.object(db.config, "SCORE_THRESHOLD_ANALYSIS", 5.0):
            self.assertEqual(db.count_analysis_candidates()["above_threshold"], 1)


if __name__ == "__main__":
    unittest.main()
