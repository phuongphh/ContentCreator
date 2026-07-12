"""Tests for collectors/hf_drama_importer.py (issue #78 follow-up)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import collectors.hf_drama_importer as hf
import storage.database as db
import storage.migrate as migrate
import storage.stories as stories


def _page(rows, columns=("title", "body", "id"), num_rows_total=None):
    return {
        "features": [{"feature_idx": i, "name": c} for i, c in enumerate(columns)],
        "rows": [{"row_idx": i, "row": r, "truncated_cells": []} for i, r in enumerate(rows)],
        "num_rows_total": num_rows_total if num_rows_total is not None else len(rows),
        "num_rows_per_page": 100,
        "partial": False,
    }


class _HFDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestPickColumn(unittest.TestCase):
    def test_override_when_present(self):
        self.assertEqual(hf._pick_column(["a", "b"], "b", ["x"]), "b")

    def test_override_missing_raises(self):
        with self.assertRaises(hf.HFImportError):
            hf._pick_column(["a"], "zzz", ["a"])

    def test_first_candidate_present(self):
        self.assertEqual(hf._pick_column(["selftext", "title"], "", ["body", "selftext"]), "selftext")

    def test_none_when_no_candidate(self):
        self.assertIsNone(hf._pick_column(["x", "y"], "", ["a", "b"]))


class TestImportDataset(_HFDBTest):
    def test_imports_and_autodetects_columns(self):
        rows = [
            {"title": "AITA 1", "body": "Story one body", "id": "a1"},
            {"title": "AITA 2", "body": "Story two body", "id": "a2"},
        ]
        with patch.object(hf, "_fetch_rows", return_value=_page(rows)):
            n = hf.import_dataset(dataset="owner/ds", limit=10)
        self.assertEqual(n, 2)
        pending = stories.get_pending(track="drama")
        self.assertEqual({p["source"] for p in pending}, {"huggingface"})
        self.assertEqual({p["title"] for p in pending}, {"AITA 1", "AITA 2"})

    def test_skips_empty_and_removed_bodies(self):
        rows = [
            {"title": "ok", "body": "Real body", "id": "1"},
            {"title": "empty", "body": "   ", "id": "2"},
            {"title": "removed", "body": "[removed]", "id": "3"},
        ]
        with patch.object(hf, "_fetch_rows", return_value=_page(rows)):
            n = hf.import_dataset(dataset="owner/ds", limit=10)
        self.assertEqual(n, 1)

    def test_dedupe_on_rerun(self):
        rows = [{"title": "t", "body": "b", "id": "same1"}]
        with patch.object(hf, "_fetch_rows", return_value=_page(rows)):
            first = hf.import_dataset(dataset="owner/ds", limit=10)
            second = hf.import_dataset(dataset="owner/ds", limit=10)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # id-based source_id dedupes

    def test_content_hash_source_id_without_id_column(self):
        rows = [{"title": "t", "body": "unique body text"}]
        page = _page(rows, columns=("title", "body"))
        with patch.object(hf, "_fetch_rows", return_value=page):
            first = hf.import_dataset(dataset="owner/ds", limit=10)
            second = hf.import_dataset(dataset="owner/ds", limit=10)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # content-hash still dedupes

    def test_missing_body_column_raises(self):
        page = _page([{"headline": "x"}], columns=("headline",))
        with patch.object(hf, "_fetch_rows", return_value=page):
            with self.assertRaises(hf.HFImportError):
                hf.import_dataset(dataset="owner/ds", limit=10)

    def test_body_field_override(self):
        rows = [{"title": "t", "narrative": "the story", "id": "1"}]
        page = _page(rows, columns=("title", "narrative", "id"))
        with patch.object(hf.config, "HF_BODY_FIELD", "narrative"), \
             patch.object(hf, "_fetch_rows", return_value=page):
            n = hf.import_dataset(dataset="owner/ds", limit=10)
        self.assertEqual(n, 1)
        self.assertEqual(stories.get_pending(track="drama")[0]["raw_content"], "the story")

    def test_pagination_spans_pages(self):
        page1 = _page([{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(100)],
                      num_rows_total=150)
        page2 = _page([{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(100, 150)],
                      num_rows_total=150)
        with patch.object(hf, "_fetch_rows", side_effect=[page1, page2]) as fetch:
            n = hf.import_dataset(dataset="owner/ds", limit=150)
        self.assertEqual(n, 150)
        self.assertEqual(fetch.call_count, 2)  # needed a second page


if __name__ == "__main__":
    unittest.main()
