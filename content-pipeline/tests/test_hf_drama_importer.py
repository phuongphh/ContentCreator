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
import storage.pipeline_state as ps
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

    def test_newest_pulls_from_tail(self):
        # --newest: probe size (1000 rows), then import from offset 1000-30=970.
        rows = [{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(970, 1000)]
        probe = _page([{"title": "x", "body": "y", "id": "0"}], num_rows_total=1000)
        page = _page(rows, num_rows_total=1000)
        with patch.object(hf, "_fetch_rows", side_effect=[probe, page]) as fetch:
            n = hf.import_dataset(dataset="owner/ds", limit=30, newest=True)
        self.assertEqual(n, 30)
        # The real fetch (2nd call) started at offset 970, not 0.
        second_call_offset = fetch.call_args_list[1][0][3]
        self.assertEqual(second_call_offset, 970)

    def test_newest_offset_clamped_when_dataset_smaller_than_limit(self):
        probe = _page([{"title": "x", "body": "y", "id": "0"}], num_rows_total=5)
        rows = [{"title": f"t{i}", "body": f"b{i}", "id": str(i)} for i in range(5)]
        page = _page(rows, num_rows_total=5)
        with patch.object(hf, "_fetch_rows", side_effect=[probe, page]) as fetch:
            n = hf.import_dataset(dataset="owner/ds", limit=30, newest=True)
        self.assertEqual(n, 5)
        self.assertEqual(fetch.call_args_list[1][0][3], 0)  # offset clamped to 0

    def test_pagination_spans_pages(self):
        page1 = _page([{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(100)],
                      num_rows_total=150)
        page2 = _page([{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(100, 150)],
                      num_rows_total=150)
        with patch.object(hf, "_fetch_rows", side_effect=[page1, page2]) as fetch:
            n = hf.import_dataset(dataset="owner/ds", limit=150)
        self.assertEqual(n, 150)
        self.assertEqual(fetch.call_count, 2)  # needed a second page


class TestImportDaily(_HFDBTest):
    """Cursor-based daily import (issue #90) — forward-walks a static dump."""

    def _key(self, dataset="owner/ds", split="train"):
        return f"hf_cursor:{dataset}:{split}"

    def test_first_run_starts_at_zero_and_advances_cursor(self):
        rows = [{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(10)]
        probe = _page([{"title": "x", "body": "y", "id": "p"}], num_rows_total=1000)
        page = _page(rows, num_rows_total=1000)
        with patch.object(hf, "_fetch_rows", side_effect=[probe, page]) as fetch:
            n = hf.import_daily(dataset="owner/ds", limit=10)
        self.assertEqual(n, 10)
        # Real import fetch (2nd call) started at offset 0.
        self.assertEqual(fetch.call_args_list[1][0][3], 0)
        # Cursor advanced by rows scanned (10).
        self.assertEqual(ps.get_int(self._key()), 10)

    def test_second_run_continues_from_cursor(self):
        ps.set_int(self._key(), 40)
        rows = [{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(40, 50)]
        probe = _page([{"title": "x", "body": "y", "id": "p"}], num_rows_total=1000)
        page = _page(rows, num_rows_total=1000)
        with patch.object(hf, "_fetch_rows", side_effect=[probe, page]) as fetch:
            n = hf.import_daily(dataset="owner/ds", limit=10)
        self.assertEqual(n, 10)
        self.assertEqual(fetch.call_args_list[1][0][3], 40)  # resumed at 40
        self.assertEqual(ps.get_int(self._key()), 50)

    def test_cursor_advances_past_skipped_empties(self):
        # Window has an empty row in the middle: importing the target (2) still
        # scans 3 rows, so the cursor must advance by scanned (3), not imported.
        rows = [
            {"title": "a", "body": "real one", "id": "1"},
            {"title": "b", "body": "   ", "id": "2"},
            {"title": "c", "body": "real two", "id": "3"},
        ]
        probe = _page([{"title": "x", "body": "y", "id": "p"}], num_rows_total=1000)
        page = _page(rows, num_rows_total=1000)
        with patch.object(hf, "_fetch_rows", side_effect=[probe, page]):
            n = hf.import_daily(dataset="owner/ds", limit=2)
        self.assertEqual(n, 2)
        self.assertEqual(ps.get_int(self._key()), 3)  # scanned, not imported

    def test_wraps_to_zero_at_end(self):
        # Cursor already at the end -> wrap to 0 before importing.
        ps.set_int(self._key(), 1000)
        rows = [{"title": f"t{i}", "body": f"b{i}", "id": str(i)} for i in range(10)]
        probe = _page([{"title": "x", "body": "y", "id": "p"}], num_rows_total=1000)
        page = _page(rows, num_rows_total=1000)
        with patch.object(hf, "_fetch_rows", side_effect=[probe, page]) as fetch:
            hf.import_daily(dataset="owner/ds", limit=10)
        self.assertEqual(fetch.call_args_list[1][0][3], 0)  # wrapped to start
        self.assertEqual(ps.get_int(self._key()), 10)

    def test_cursor_wraps_when_reaching_exact_end(self):
        # Import the final window -> new offset == total -> reset to 0 for next run.
        ps.set_int(self._key(), 90)
        rows = [{"title": f"t{i}", "body": f"b{i}", "id": str(i)} for i in range(90, 100)]
        probe = _page([{"title": "x", "body": "y", "id": "p"}], num_rows_total=100)
        page = _page(rows, num_rows_total=100)
        with patch.object(hf, "_fetch_rows", side_effect=[probe, page]):
            hf.import_daily(dataset="owner/ds", limit=10)
        self.assertEqual(ps.get_int(self._key()), 0)  # wrapped for next run

    def test_fetch_failure_does_not_advance_cursor(self):
        ps.set_int(self._key(), 20)
        probe = _page([{"title": "x", "body": "y", "id": "p"}], num_rows_total=1000)
        with patch.object(hf, "_fetch_rows",
                          side_effect=[probe, hf.HFImportError("boom")]):
            with self.assertRaises(hf.HFImportError):
                hf.import_daily(dataset="owner/ds", limit=10)
        self.assertEqual(ps.get_int(self._key()), 20)  # unchanged


if __name__ == "__main__":
    unittest.main()
