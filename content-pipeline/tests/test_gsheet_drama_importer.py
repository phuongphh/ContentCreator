"""Tests for collectors/gsheet_drama_importer.py (Google Sheets drama bridge)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.stories as stories
import collectors.gsheet_drama_importer as importer


LONG_BODY = " ".join(["Câu chuyện drama dài đủ để vượt ngưỡng ký tự tối thiểu."] * 10)


def _csv(rows: list[list[str]]) -> str:
    import csv
    import io
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


class TestExportCsvUrl(unittest.TestCase):
    def test_edit_link_converted_to_export(self):
        url = "https://docs.google.com/spreadsheets/d/abc123XYZ_-/edit#gid=42"
        self.assertEqual(
            importer._export_csv_url(url),
            "https://docs.google.com/spreadsheets/d/abc123XYZ_-/export?format=csv&gid=42",
        )

    def test_edit_link_without_gid_defaults_to_zero(self):
        url = "https://docs.google.com/spreadsheets/d/abc123/edit"
        self.assertIn("gid=0", importer._export_csv_url(url))

    def test_published_csv_link_untouched(self):
        url = "https://docs.google.com/spreadsheets/d/e/2PACX/pub?output=csv"
        self.assertEqual(importer._export_csv_url(url), url)

    def test_non_google_url_untouched(self):
        url = "https://example.com/stories.csv"
        self.assertEqual(importer._export_csv_url(url), url)


class TestResolveColumns(unittest.TestCase):
    def test_english_headers(self):
        cols = importer._resolve_columns(["Title", "Content", "URL", "Source"])
        self.assertEqual(cols, {"title": 0, "content": 1, "url": 2, "source": 3})

    def test_vietnamese_headers_with_accents(self):
        cols = importer._resolve_columns(["Tiêu đề", "Nội dung", "Link"])
        self.assertEqual(cols["title"], 0)
        self.assertEqual(cols["content"], 1)
        self.assertEqual(cols["url"], 2)

    def test_missing_content_column_raises(self):
        with self.assertRaises(importer.GSheetFetchError):
            importer._resolve_columns(["Title", "Ngày"])


class TestCleanHtml(unittest.TestCase):
    def test_strips_tags_and_entities(self):
        html_in = "<p>My MIL said &quot;no&quot;.<br/>Then she left.</p>"
        out = importer._clean_html(html_in)
        self.assertNotIn("<", out)
        self.assertIn('"no"', out)
        self.assertIn("Then she left.", out)


class TestCollectAllGsheet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()
        self._url_patch = patch.object(
            importer.config, "GSHEET_DRAMA_URL",
            "https://docs.google.com/spreadsheets/d/test/edit")
        self._url_patch.start()

    def tearDown(self):
        self._url_patch.stop()
        self._patch.stop()

    def _run_with_sheet(self, rows):
        with patch.object(importer, "_fetch_csv_text", return_value=_csv(rows)):
            return importer.collect_all_gsheet()

    def test_disabled_when_url_empty(self):
        with patch.object(importer.config, "GSHEET_DRAMA_URL", ""):
            self.assertEqual(importer.collect_all_gsheet(), 0)

    def test_imports_rows_and_dedupes_rerun(self):
        rows = [["Title", "Content", "URL"],
                ["Chuyện mẹ chồng", LONG_BODY, "https://r.example/1"],
                ["Chuyện công ty", LONG_BODY, "https://r.example/2"]]
        self.assertEqual(self._run_with_sheet(rows), 2)
        # Same sheet fetched again → everything already imported.
        self.assertEqual(self._run_with_sheet(rows), 0)
        pending = stories.get_pending(limit=10, track="drama")
        self.assertEqual(len(pending), 2)
        self.assertTrue(all(s["source"] == "gsheet" for s in pending))

    def test_row_without_url_dedupes_by_content_hash(self):
        rows = [["Title", "Content"], ["Dán tay", LONG_BODY]]
        self.assertEqual(self._run_with_sheet(rows), 1)
        self.assertEqual(self._run_with_sheet(rows), 0)

    def test_thin_rows_skipped(self):
        rows = [["Title", "Content"],
                ["Chỉ có link", "ngắn quá"],
                ["", LONG_BODY]]
        self.assertEqual(self._run_with_sheet(rows), 0)

    def test_import_limit_respected(self):
        rows = [["Title", "Content", "URL"]] + [
            [f"Story {i}", LONG_BODY, f"https://r.example/{i}"] for i in range(10)
        ]
        with patch.object(importer.config, "GSHEET_IMPORT_LIMIT", 3):
            self.assertEqual(self._run_with_sheet(rows), 3)
        # Next run picks up where the cap stopped (dedupe skips the first 3).
        with patch.object(importer.config, "GSHEET_IMPORT_LIMIT", 30):
            self.assertEqual(self._run_with_sheet(rows), 7)

    def test_html_content_cleaned_before_insert(self):
        body_html = "<p>" + LONG_BODY + "</p><br/>"
        rows = [["Title", "Content"], ["HTML row", body_html]]
        self.assertEqual(self._run_with_sheet(rows), 1)
        story = stories.get_pending(limit=1, track="drama")[0]
        self.assertNotIn("<p>", story["raw_content"])

    def test_html_login_page_raises_clear_error(self):
        with patch.object(importer, "_fetch_csv_text",
                          side_effect=importer.GSheetFetchError("got an HTML page")):
            with self.assertRaises(importer.GSheetFetchError):
                importer.collect_all_gsheet()


if __name__ == "__main__":
    unittest.main()
