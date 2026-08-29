"""Tests cho processors/ai_analyzer.py (Phase 1d — Deep Analysis, issue #117)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import processors.ai_analyzer as analyzer

_ANALYSIS = {
    "category": "news",
    "urgency": "immediate",
    "hooks": ["hook"],
    "viet_angle": "góc VN",
    "youtube_titles": ["t1"],
    "tiktok_hashtags": ["#ai"],
    "production_difficulty": "easy",
    "difficulty_reason": "dễ",
    "one_line_summary": "tóm tắt",
}
BODY = "Nội dung thật của bài viết về AI cho người đi làm văn phòng." * 2


def _fake_message(text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


class AnalyzerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._patch = patch.object(db.config, "DB_PATH",
                                   os.path.join(self.tmp, "test.db"))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        db.init_db()
        # log_token_usage ghi vào bảng cost_logs (migration 007) — không có ở
        # DB test tối thiểu, và không phải thứ đang kiểm chứng.
        usage = patch("processors.ai_usage.log_token_usage")
        usage.start()
        self.addCleanup(usage.stop)

    def _add(self, url, *, title="Tin AI", raw_content=None, summary=None,
             score=9.0, source="feed") -> int:
        conn = db.get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO articles (source, title, url, raw_content, summary, ai_score) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source, title, url, raw_content, summary, score))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _row(self, article_id):
        conn = db.get_connection()
        try:
            return dict(conn.execute("SELECT * FROM articles WHERE id = ?",
                                     (article_id,)).fetchone())
        finally:
            conn.close()

    def _mock_client(self, reply=None):
        client = MagicMock()
        client.messages.create.return_value = _fake_message(
            reply if reply is not None else json.dumps(_ANALYSIS))
        return client


class TestAnalyzeArticlePrompt(AnalyzerTestBase):
    def test_prompt_includes_title_and_source(self):
        client = self._mock_client()
        with patch.object(analyzer.anthropic, "Anthropic", return_value=client):
            result = analyzer.analyze_article(BODY, title="ChatGPT có tính năng mới",
                                              source="TechCrunch")
        self.assertEqual(result["category"], "news")
        prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("ChatGPT có tính năng mới", prompt)
        self.assertIn("TechCrunch", prompt)
        self.assertIn(BODY[:50], prompt)

    def test_prompt_still_valid_without_title(self):
        """Caller cũ chỉ truyền nội dung — không được vỡ vì thiếu tham số."""
        client = self._mock_client()
        with patch.object(analyzer.anthropic, "Anthropic", return_value=client):
            self.assertIsNotNone(analyzer.analyze_article(BODY))
        prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("(không có)", prompt)
        self.assertNotIn("{title}", prompt)

    def test_content_truncated_to_4000_chars(self):
        client = self._mock_client()
        with patch.object(analyzer.anthropic, "Anthropic", return_value=client):
            analyzer.analyze_article("x" * 9000, title="T")
        prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(prompt.count("x"), 4000)

    def test_returns_none_when_reply_never_parses(self):
        client = self._mock_client(reply="xin lỗi, tôi không thể")
        with patch.object(analyzer.anthropic, "Anthropic", return_value=client):
            self.assertIsNone(analyzer.analyze_article(BODY, title="T"))
        self.assertEqual(client.messages.create.call_count, 3)


class TestAnalyzeTopArticles(AnalyzerTestBase):
    def test_content_less_articles_are_swept_not_retried(self):
        """Regression #117: bài rỗng rời pool ngay, không quay lại ngày hôm sau."""
        empties = [self._add(f"http://empty/{i}", summary="") for i in range(12)]
        good = self._add("http://good", raw_content=BODY, score=6.0)

        client = self._mock_client()
        with patch.object(analyzer.anthropic, "Anthropic", return_value=client):
            analyzed = analyzer.analyze_top_articles()

        self.assertEqual(analyzed, 1)
        self.assertEqual(client.messages.create.call_count, 1,
                         "chỉ bài có nội dung mới được gọi Sonnet")
        for article_id in empties:
            self.assertEqual(self._row(article_id)["status"], "skipped")
        self.assertIsNotNone(self._row(good)["ai_analysis"])

        # Lần chạy sau: pool sạch, không còn bài rỗng nào để quét.
        with patch.object(analyzer.anthropic, "Anthropic",
                          return_value=self._mock_client()):
            self.assertEqual(analyzer.analyze_top_articles(), 0)

    def test_marks_row_skipped_if_a_content_less_row_slips_through(self):
        """Lớp phòng thủ 2 — pool bị bơm thẳng bài rỗng (bỏ qua bộ lọc SQL)."""
        empty = self._add("http://empty", summary="")
        with patch.object(analyzer, "mark_articles_unanalyzable", return_value=0), \
             patch.object(analyzer, "get_articles_for_analysis",
                          return_value=[self._row(empty)]), \
             patch.object(analyzer.anthropic, "Anthropic",
                          return_value=self._mock_client()) as anthropic_cls:
            self.assertEqual(analyzer.analyze_top_articles(), 0)
        self.assertEqual(self._row(empty)["status"], "skipped")
        anthropic_cls.return_value.messages.create.assert_not_called()

    def test_passes_title_and_source_of_each_article(self):
        self._add("http://a", title="Gemini 3 ra mắt", source="The Verge",
                  raw_content=BODY)
        client = self._mock_client()
        with patch.object(analyzer.anthropic, "Anthropic", return_value=client):
            analyzer.analyze_top_articles()
        prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Gemini 3 ra mắt", prompt)
        self.assertIn("The Verge", prompt)

    def test_uses_richer_summary_when_raw_content_is_shorter(self):
        self._add("http://a", raw_content="cụt", summary=BODY)
        client = self._mock_client()
        with patch.object(analyzer.anthropic, "Anthropic", return_value=client):
            analyzer.analyze_top_articles()
        prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn(BODY[:50], prompt)

    def test_failed_analysis_leaves_article_pending_for_retry(self):
        """Lỗi gọi model là TẠM THỜI — bài phải ở lại pool, không bị skipped."""
        article_id = self._add("http://a", raw_content=BODY)
        client = self._mock_client(reply="không phải JSON")
        with patch.object(analyzer.anthropic, "Anthropic", return_value=client):
            self.assertEqual(analyzer.analyze_top_articles(), 0)
        row = self._row(article_id)
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["ai_analysis"])

    def test_stores_category_and_urgency(self):
        article_id = self._add("http://a", raw_content=BODY)
        with patch.object(analyzer.anthropic, "Anthropic",
                          return_value=self._mock_client()):
            analyzer.analyze_top_articles()
        row = self._row(article_id)
        self.assertEqual(row["category"], "news")
        self.assertEqual(row["urgency"], "immediate")
        self.assertEqual(json.loads(row["ai_analysis"])["viet_angle"], "góc VN")

    def test_empty_pool_is_not_an_error(self):
        with patch.object(analyzer.anthropic, "Anthropic",
                          return_value=self._mock_client()) as cls:
            self.assertEqual(analyzer.analyze_top_articles(), 0)
        cls.return_value.messages.create.assert_not_called()


class TestNoAnalysisReason(AnalyzerTestBase):
    """Dòng giải thích gửi vào pipeline summary khi Phase 1d ra 0 bài."""

    def test_empty_pool_says_no_articles_waiting(self):
        reason = analyzer.no_analysis_reason()
        self.assertIn("không còn bài", reason)

    def test_pool_without_content_says_so(self):
        for i in range(7):
            self._add(f"http://empty/{i}", summary="")
        reason = analyzer.no_analysis_reason()
        self.assertIn("7 bài", reason)
        self.assertIn("KHÔNG", reason)

    def test_usable_pool_points_at_the_model_call(self):
        self._add("http://ok", raw_content=BODY)
        self.assertIn("Sonnet", analyzer.no_analysis_reason())

    def test_db_error_still_returns_a_message(self):
        with patch.object(analyzer, "count_analysis_candidates",
                          side_effect=RuntimeError("db locked")):
            self.assertIn("0 bài", analyzer.no_analysis_reason())


if __name__ == "__main__":
    unittest.main()
