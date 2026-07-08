"""Tests for storage/cost_logs.py + ai_usage persistence (Phase 6)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.cost_logs as cl
from processors import ai_usage


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()


class TestRecord(Base):
    def test_record_and_daily_totals(self):
        cl.record_cost("anthropic", "claude-haiku-4-5", "scorer",
                       input_tokens=500, output_tokens=50, date="2026-07-01")
        cl.record_cost("anthropic", "claude-sonnet-4-5", "rewriter",
                       input_tokens=2000, output_tokens=400, date="2026-07-01")
        totals = cl.daily_totals(since="2026-07-01")
        by_service = {(r["date"], r["service"]): r for r in totals}
        row = by_service[("2026-07-01", "anthropic")]
        self.assertEqual(row["input_tokens"], 2500)
        self.assertEqual(row["output_tokens"], 450)
        self.assertEqual(row["calls"], 2)

    def test_rows_since_filters(self):
        cl.record_cost("anthropic", "m", "l", input_tokens=1, date="2026-06-01")
        cl.record_cost("anthropic", "m", "l", input_tokens=2, date="2026-07-01")
        rows = cl.rows_since("2026-06-15")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["input_tokens"], 2)


class _FakeUsage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _FakeMessage:
    def __init__(self, model, i, o):
        self.model = model
        self.usage = _FakeUsage(i, o)


class TestAiUsagePersistence(Base):
    def test_log_token_usage_persists(self):
        msg = _FakeMessage("claude-haiku-4-5", 500, 50)
        ai_usage.log_token_usage("drama_scorer", 42, msg)
        rows = cl.rows_since("2000-01-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model"], "claude-haiku-4-5")
        self.assertEqual(rows[0]["ref_id"], "42")
        self.assertEqual(rows[0]["label"], "drama_scorer")

    def test_no_usage_no_row(self):
        class NoUsage:
            usage = None
        ai_usage.log_token_usage("x", 1, NoUsage())
        self.assertEqual(cl.rows_since("2000-01-01"), [])

    def test_non_int_usage_does_not_raise(self):
        class Weird:
            class usage:  # noqa
                input_tokens = "NaN"
                output_tokens = "NaN"
            model = "claude-haiku-4-5"
        # Must not raise even though int("NaN") fails inside persistence.
        ai_usage.log_token_usage("x", 1, Weird())


if __name__ == "__main__":
    unittest.main()
