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


class _FakeResp:
    """Minimal urlopen() context-manager stand-in."""
    def __init__(self, body: bytes):
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _ChunkResp:
    """urlopen() stand-in that supports chunked read(n) for streaming downloads."""
    def __init__(self, body: bytes):
        self._buf = body
        self._pos = 0
    def read(self, n=-1):
        if n is None or n < 0:
            chunk, self._pos = self._buf[self._pos:], len(self._buf)
            return chunk
        chunk = self._buf[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class TestFetchRowsClassifiesFailures(unittest.TestCase):
    """_fetch_rows tells a soft 'viewer unavailable' apart from a hard error."""

    def test_non_json_body_raises_unavailable(self):
        # HTML gateway/"viewer unavailable" page -> "Unexpected token '<'".
        html = _FakeResp(b"<!DOCTYPE html><html>502 Bad Gateway</html>")
        with patch.object(hf, "urlopen", return_value=html), \
             patch.object(hf.time, "sleep"):
            with self.assertRaises(hf.HFDatasetUnavailableError):
                hf._fetch_rows("owner/ds", "default", "train", 0, 10)

    def test_5xx_raises_unavailable(self):
        from urllib.error import HTTPError
        err = HTTPError("u", 503, "Service Unavailable", {}, None)
        with patch.object(hf, "urlopen", side_effect=err), \
             patch.object(hf.time, "sleep"):
            with self.assertRaises(hf.HFDatasetUnavailableError):
                hf._fetch_rows("owner/ds", "default", "train", 0, 10)

    def test_404_raises_plain_import_error(self):
        from urllib.error import HTTPError
        err = HTTPError("u", 404, "Not Found", {}, None)
        with patch.object(hf, "urlopen", side_effect=err), \
             patch.object(hf.time, "sleep"):
            with self.assertRaises(hf.HFImportError) as ctx:
                hf._fetch_rows("owner/ds", "default", "train", 0, 10)
        self.assertNotIsInstance(ctx.exception, hf.HFDatasetUnavailableError)

    def test_network_error_raises_plain_import_error(self):
        from urllib.error import URLError
        with patch.object(hf, "urlopen", side_effect=URLError("timeout")), \
             patch.object(hf.time, "sleep"):
            with self.assertRaises(hf.HFImportError) as ctx:
                hf._fetch_rows("owner/ds", "default", "train", 0, 10)
        self.assertNotIsInstance(ctx.exception, hf.HFDatasetUnavailableError)

    def test_valid_json_returns_parsed(self):
        good = _FakeResp(b'{"rows": [], "features": [], "num_rows_total": 0}')
        with patch.object(hf, "urlopen", return_value=good):
            out = hf._fetch_rows("owner/ds", "default", "train", 0, 10)
        self.assertEqual(out["num_rows_total"], 0)


class TestImportDaily(_HFDBTest):
    """Cursor-based daily import (issue #90) — forward-walks a static dump."""

    def _key(self, dataset="owner/ds", cfg="default", split="train"):
        return f"hf_cursor:{dataset}:{cfg}:{split}"

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

    def test_dataset_unavailable_propagates_and_keeps_cursor(self):
        # Viewer down AND the raw-CSV fallback also down (issue #92): import_daily
        # raises the soft error and the cursor stays put so tomorrow retries.
        ps.set_int(self._key(), 30)
        with patch.object(hf, "_fetch_rows",
                          side_effect=hf.HFDatasetUnavailableError("viewer down")), \
             patch.object(hf, "_ensure_csv_cached",
                          side_effect=hf.HFDatasetUnavailableError("hub down too")):
            with self.assertRaises(hf.HFDatasetUnavailableError):
                hf.import_daily(dataset="owner/ds", limit=10)
        self.assertEqual(ps.get_int(self._key()), 30)  # unchanged


class TestQualityComments(_HFDBTest):
    """Loading quality comments alongside the story body."""

    def _import(self, rows, columns):
        with patch.object(hf, "_fetch_rows", return_value=_page(rows, columns=columns)):
            hf.import_dataset(dataset="owner/ds", limit=10)
        return stories.get_pending(track="drama")

    def test_parse_json_list_of_scored_dicts(self):
        val = '[{"body": "you are NTA clearly", "score": 120}, {"body": "YTA", "score": 3}]'
        got = hf._parse_comments(val)
        self.assertEqual(got[0]["content"], "you are NTA clearly")
        self.assertEqual(got[0]["score"], 120)

    def test_parse_json_list_of_strings(self):
        got = hf._parse_comments('["first reply", "second reply"]')
        self.assertEqual([c["content"] for c in got], ["first reply", "second reply"])
        self.assertIsNone(got[0]["score"])

    def test_parse_plain_string_single_comment(self):
        got = hf._parse_comments("just one top comment here")
        self.assertEqual(got, [{"content": "just one top comment here", "score": None}])

    def test_parse_empty_returns_nothing(self):
        self.assertEqual(hf._parse_comments(""), [])
        self.assertEqual(hf._parse_comments(None), [])

    def test_select_filters_and_ranks(self):
        comments = [
            {"content": "short", "score": 999},                        # too short
            {"content": "a solid high-scored community reaction here", "score": 80},
            {"content": "a solid low-scored community reaction here", "score": 2},   # low score
            {"content": "another strong well-upvoted reaction to this", "score": 150},
        ]
        with patch.object(hf.config, "HF_COMMENT_MIN_SCORE", 10), \
             patch.object(hf.config, "HF_COMMENT_MIN_CHARS", 40), \
             patch.object(hf.config, "HF_COMMENT_TOP_N", 3):
            got = hf._select_quality_comments(comments)
        # Best-score first, low-scored + short dropped.
        self.assertEqual(got[0], "another strong well-upvoted reaction to this")
        self.assertEqual(len(got), 2)

    def test_comments_appended_to_raw_content_and_metadata(self):
        rows = [{
            "title": "AITA", "body": "the original story body text here", "id": "1",
            "top_comments": '[{"body": "This is a strong NTA verdict from the crowd", "score": 200}]',
        }]
        pending = self._import(rows, columns=("title", "body", "id", "top_comments"))
        self.assertEqual(len(pending), 1)
        story = pending[0]
        self.assertIn("TOP COMMENTS FROM REDDIT", story["raw_content"])
        self.assertIn("strong NTA verdict", story["raw_content"])
        # get_pending deserializes metadata to a dict.
        self.assertEqual(len(story["metadata"]["top_comments"]), 1)

    def test_no_comment_column_is_noop(self):
        pending = self._import([{"title": "t", "body": "a body", "id": "1"}],
                               columns=("title", "body", "id"))
        self.assertNotIn("TOP COMMENTS", pending[0]["raw_content"])

    def test_comments_do_not_change_source_id(self):
        # Same row with and without comments must dedupe (source_id from body only).
        base = {"title": "t", "body": "identical body", "id": "x"}
        with_c = dict(base, top_comments='["a long enough community reaction text"]')
        with patch.object(hf, "_fetch_rows",
                          return_value=_page([base], columns=("title", "body", "id"))):
            first = hf.import_dataset(dataset="owner/ds", limit=10)
        with patch.object(hf, "_fetch_rows",
                          return_value=_page([with_c], columns=("title", "body", "id", "top_comments"))):
            second = hf.import_dataset(dataset="owner/ds", limit=10)
        self.assertEqual((first, second), (1, 0))

    def test_disabled_skips_comments(self):
        rows = [{"title": "t", "body": "a body", "id": "1",
                 "top_comments": '["a long enough community reaction text here"]'}]
        with patch.object(hf.config, "HF_IMPORT_COMMENTS", False):
            pending = self._import(rows, columns=("title", "body", "id", "top_comments"))
        self.assertNotIn("TOP COMMENTS", pending[0]["raw_content"])


def _write_csv(path, rows, columns=("title", "body", "id")):
    import csv as _csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=list(columns))
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


class TestCsvFallback(_HFDBTest):
    """Raw-CSV fallback when datasets-server is unavailable (issue #92)."""

    def _key(self, dataset="owner/ds", cfg="default", split="train"):
        return f"hf_cursor:{dataset}:{cfg}:{split}"

    def _csv_with(self, rows, columns=("title", "body", "id")):
        path = os.path.join(self.tmp, "ds.csv")
        _write_csv(path, rows, columns)
        return path

    def test_import_dataset_falls_back_to_csv_when_api_down(self):
        rows = [
            {"title": "A", "body": "body a", "id": "1"},
            {"title": "B", "body": "body b", "id": "2"},
        ]
        csv_path = self._csv_with(rows)
        with patch.object(hf, "_fetch_rows",
                          side_effect=hf.HFDatasetUnavailableError("viewer down")), \
             patch.object(hf, "_ensure_csv_cached", return_value=csv_path):
            n = hf.import_dataset(dataset="owner/ds", limit=10)
        self.assertEqual(n, 2)
        titles = {p["title"] for p in stories.get_pending(track="drama")}
        self.assertEqual(titles, {"A", "B"})

    def test_import_daily_falls_back_and_advances_cursor(self):
        rows = [{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(10)]
        csv_path = self._csv_with(rows)
        with patch.object(hf, "_fetch_rows",
                          side_effect=hf.HFDatasetUnavailableError("viewer down")), \
             patch.object(hf, "_ensure_csv_cached", return_value=csv_path):
            n = hf.import_daily(dataset="owner/ds", limit=4)
        self.assertEqual(n, 4)
        # Cursor advanced by rows scanned from offset 0 (4), same as the API path.
        self.assertEqual(ps.get_int(self._key()), 4)

    def test_csv_cursor_resumes_from_offset(self):
        rows = [{"title": f"t{i}", "body": f"body{i}", "id": str(i)} for i in range(10)]
        csv_path = self._csv_with(rows)
        ps.set_int(self._key(), 6)
        with patch.object(hf, "_fetch_rows",
                          side_effect=hf.HFDatasetUnavailableError("viewer down")), \
             patch.object(hf, "_ensure_csv_cached", return_value=csv_path):
            n = hf.import_daily(dataset="owner/ds", limit=10)
        # Only rows 6..9 remain (4), and the cursor wraps at end (10 rows total).
        self.assertEqual(n, 4)
        self.assertEqual(ps.get_int(self._key()), 0)

    def test_csv_scanned_counts_skipped_rows(self):
        rows = [
            {"title": "a", "body": "real one", "id": "1"},
            {"title": "b", "body": "   ", "id": "2"},         # empty -> skipped
            {"title": "c", "body": "[removed]", "id": "3"},   # removed -> skipped
            {"title": "d", "body": "real two", "id": "4"},
        ]
        csv_path = self._csv_with(rows)
        with patch.object(hf, "_fetch_rows",
                          side_effect=hf.HFDatasetUnavailableError("viewer down")), \
             patch.object(hf, "_ensure_csv_cached", return_value=csv_path):
            n = hf.import_daily(dataset="owner/ds", limit=2)
        self.assertEqual(n, 2)
        # Scanned 4 rows to import 2 -> cursor advances by scanned, wraps at end.
        self.assertEqual(ps.get_int(self._key()), 0)

    def test_source_id_consistent_between_api_and_csv(self):
        # THE consistency guarantee: the same row imported via API then via CSV
        # must dedupe (identical source_id), so an API<->CSV switch mid-dataset
        # never double-imports.
        row = {"title": "t", "body": "shared body", "id": "same"}
        with patch.object(hf, "_fetch_rows", return_value=_page([row])):
            first = hf.import_dataset(dataset="owner/ds", limit=10)
        csv_path = self._csv_with([row])
        with patch.object(hf, "_fetch_rows",
                          side_effect=hf.HFDatasetUnavailableError("viewer down")), \
             patch.object(hf, "_ensure_csv_cached", return_value=csv_path):
            second = hf.import_dataset(dataset="owner/ds", limit=10)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # CSV path produced the same source_id

    def test_force_csv_skips_api_entirely(self):
        rows = [{"title": "A", "body": "body a", "id": "1"}]
        csv_path = self._csv_with(rows)
        with patch.object(hf, "_fetch_rows") as fetch, \
             patch.object(hf, "_ensure_csv_cached", return_value=csv_path):
            n = hf.import_dataset(dataset="owner/ds", limit=10, force_csv=True)
        self.assertEqual(n, 1)
        fetch.assert_not_called()  # API never touched

    def test_fallback_disabled_propagates_soft_error(self):
        with patch.object(hf.config, "HF_CSV_FALLBACK_ENABLED", False), \
             patch.object(hf, "_fetch_rows",
                          side_effect=hf.HFDatasetUnavailableError("viewer down")):
            with self.assertRaises(hf.HFDatasetUnavailableError):
                hf.import_dataset(dataset="owner/ds", limit=10)

    def test_hard_error_not_masked_by_fallback(self):
        # A plain HFImportError (e.g. 404 misconfig) is NOT a viewer-down signal,
        # so the CSV fallback must not swallow it.
        with patch.object(hf, "_fetch_rows", side_effect=hf.HFImportError("404")), \
             patch.object(hf, "_ensure_csv_cached") as ens:
            with self.assertRaises(hf.HFImportError):
                hf.import_dataset(dataset="owner/ds", limit=10)
        ens.assert_not_called()


class TestCsvDiscoveryAndDownload(unittest.TestCase):
    def test_resolve_csv_file_override_wins(self):
        with patch.object(hf.config, "HF_DRAMA_CSV_FILE", "my_data.csv"):
            self.assertEqual(hf._resolve_csv_file("owner/ds"), "my_data.csv")

    def test_resolve_csv_file_discovers_via_hub(self):
        meta = b'{"siblings":[{"rfilename":"README.md"},{"rfilename":"cleaned.csv"}]}'
        with patch.object(hf.config, "HF_DRAMA_CSV_FILE", ""), \
             patch.object(hf, "_hub_get", return_value=meta):
            self.assertEqual(hf._resolve_csv_file("owner/ds"), "cleaned.csv")

    def test_resolve_csv_file_no_csv_raises_hard_error(self):
        meta = b'{"siblings":[{"rfilename":"README.md"}]}'
        with patch.object(hf.config, "HF_DRAMA_CSV_FILE", ""), \
             patch.object(hf, "_hub_get", return_value=meta):
            with self.assertRaises(hf.HFImportError):
                hf._resolve_csv_file("owner/ds")

    def test_cache_path_no_traversal(self):
        # A hostile repo path must not escape the cache dir.
        p = hf._cache_path("owner/ds", "../../etc/passwd")
        self.assertTrue(os.path.abspath(p).startswith(
            os.path.abspath(hf.config.HF_CSV_CACHE_DIR)))
        self.assertNotIn("..", os.path.basename(p))

    def test_download_aborts_over_size_cap(self):
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "out.csv")
        resp = _ChunkResp(b"x" * (5 * 1024 * 1024))
        with patch.object(hf.config, "HF_CSV_MAX_BYTES", 1024), \
             patch.object(hf.config, "HF_CSV_CACHE_DIR", tmp), \
             patch.object(hf, "urlopen", return_value=resp):
            with self.assertRaises(hf.HFImportError):
                hf._download_csv("owner/ds", "d.csv", dest)
        # Partial file cleaned up; final file never created.
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_download_streams_and_renames_atomically(self):
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "out.csv")
        resp = _ChunkResp(b"title,body,id\nA,body a,1\n")
        with patch.object(hf.config, "HF_CSV_MAX_BYTES", 10 * 1024 * 1024), \
             patch.object(hf.config, "HF_CSV_CACHE_DIR", tmp), \
             patch.object(hf, "urlopen", return_value=resp):
            hf._download_csv("owner/ds", "d.csv", dest)
        with open(dest, encoding="utf-8") as f:
            self.assertIn("body a", f.read())
        self.assertFalse(os.path.exists(dest + ".part"))


if __name__ == "__main__":
    unittest.main()
