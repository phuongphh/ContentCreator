"""Tests cho collectors/rss_collector.py (thu thập RSS — issue #117).

Trọng tâm: entry chỉ có tiêu đề KHÔNG được lưu vào DB. Bản cũ lưu chúng với
raw_content/summary rỗng, và chính đám bài đó về sau chiếm hết slot phân tích
sâu khiến pipeline ra 0 video (xem tests/test_article_selection.py).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import collectors.rss_collector as rss

BODY = "OpenAI vừa công bố tính năng mới giúp dân văn phòng tự động hoá báo cáo."


def _entry(**kwargs) -> dict:
    entry = {"title": "Tin AI", "link": "https://example.com/a"}
    entry.update(kwargs)
    return entry


class _FakeFeed:
    """Đủ giống đối tượng feedparser trả về cho collect_feed."""

    def __init__(self, entries, title="Feed Test", bozo=False):
        self.entries = entries
        self.feed = {"title": title}
        self.bozo = bozo

    def get(self, key, default=None):
        return getattr(self, key, default)


class TestExtractContent(unittest.TestCase):
    def test_prefers_content_body_over_summary(self):
        summary, raw = rss._extract_content(_entry(
            summary="tóm tắt ngắn",
            content=[{"value": f"<p>{BODY}</p>"}],
        ))
        self.assertEqual(summary, "tóm tắt ngắn")
        self.assertEqual(raw, BODY)

    def test_joins_multiple_content_parts(self):
        _, raw = rss._extract_content(_entry(
            content=[{"value": "<p>Phần một.</p>"}, {"value": "<p>Phần hai.</p>"}]))
        self.assertEqual(raw, "Phần một.\n\nPhần hai.")

    def test_falls_back_to_summary_when_content_empty(self):
        """Feed để content[0] rỗng — bản cũ cho ra article rỗng (root cause #117)."""
        summary, raw = rss._extract_content(_entry(summary=BODY, content=[{"value": ""}]))
        self.assertEqual(summary, BODY)
        self.assertEqual(raw, BODY)

    def test_falls_back_to_summary_when_content_is_markup_only(self):
        summary, raw = rss._extract_content(_entry(
            summary=BODY, content=[{"value": '<img src="x.png"/>'}]))
        self.assertEqual(raw, BODY)
        self.assertEqual(summary, BODY)

    def test_uses_description_then_subtitle(self):
        self.assertEqual(rss._extract_content(_entry(description=BODY))[0], BODY)
        self.assertEqual(rss._extract_content(_entry(subtitle=BODY))[0], BODY)

    def test_summary_capped_at_500_chars(self):
        summary, raw = rss._extract_content(_entry(summary="x" * 900))
        self.assertEqual(len(summary), 500)
        self.assertEqual(len(raw), 500)

    def test_title_only_entry_yields_empty_content(self):
        summary, raw = rss._extract_content(_entry())
        self.assertEqual((summary, raw), ("", ""))

    def test_content_as_plain_string(self):
        _, raw = rss._extract_content(_entry(content=f"<div>{BODY}</div>"))
        self.assertEqual(raw, BODY)

    def test_strips_html_entities_and_whitespace(self):
        summary, _ = rss._extract_content(_entry(summary="<p>AI &amp;  bạn</p>\n"))
        self.assertEqual(summary, "AI & bạn")


class TestCollectFeed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._db = patch.object(db.config, "DB_PATH",
                                os.path.join(self.tmp, "test.db"))
        self._db.start()
        self.addCleanup(self._db.stop)
        db.init_db()
        fetch = patch.object(rss, "_fetch_feed_content", return_value="<rss/>")
        fetch.start()
        self.addCleanup(fetch.stop)

    def _collect(self, entries):
        with patch.object(rss.feedparser, "parse", return_value=_FakeFeed(entries)):
            return rss.collect_feed("https://example.com/feed")

    def _urls(self):
        conn = db.get_connection()
        try:
            return [r["url"] for r in conn.execute("SELECT url FROM articles")]
        finally:
            conn.close()

    def test_skips_title_only_entries(self):
        """Regression #117: entry không có nội dung không được vào DB."""
        count = self._collect([
            _entry(title="Chỉ có tiêu đề", link="https://example.com/empty"),
            _entry(title="Bài thật", link="https://example.com/full", summary=BODY),
        ])
        self.assertEqual(count, 1)
        self.assertEqual(self._urls(), ["https://example.com/full"])

    def test_skips_entries_whose_summary_is_markup_only(self):
        count = self._collect([_entry(link="https://example.com/img",
                                      summary='<img src="a.png"/>')])
        self.assertEqual(count, 0)
        self.assertEqual(self._urls(), [])

    def test_keeps_entry_with_body_but_no_summary(self):
        count = self._collect([_entry(link="https://example.com/c",
                                      content=[{"value": f"<p>{BODY}</p>"}])])
        self.assertEqual(count, 1)
        conn = db.get_connection()
        try:
            row = conn.execute("SELECT raw_content, summary FROM articles").fetchone()
        finally:
            conn.close()
        self.assertEqual(row["raw_content"], BODY)

    def test_still_skips_entries_without_title_or_link(self):
        count = self._collect([
            {"title": "", "link": "https://example.com/x", "summary": BODY},
            {"title": "Không có link", "link": "", "summary": BODY},
        ])
        self.assertEqual(count, 0)

    def test_duplicate_url_not_inserted_twice(self):
        entries = [_entry(link="https://example.com/dup", summary=BODY)]
        self.assertEqual(self._collect(entries), 1)
        self.assertEqual(self._collect(entries), 0)

    def test_warns_when_whole_feed_has_no_content(self):
        with self.assertLogs(rss.logger, level="WARNING") as logs:
            count = self._collect([_entry(link=f"https://example.com/{i}")
                                   for i in range(3)])
        self.assertEqual(count, 0)
        self.assertTrue(any("KHÔNG có nội dung" in line for line in logs.output))

    def test_threshold_is_respected(self):
        # config là module singleton — rss.config và db.config là cùng đối tượng.
        with patch.object(rss.config, "MIN_ARTICLE_CONTENT_CHARS", 500):
            self.assertEqual(self._collect([_entry(link="https://example.com/s",
                                                   summary=BODY)]), 0)


if __name__ == "__main__":
    unittest.main()
