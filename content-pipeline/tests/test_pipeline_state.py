"""Tests for storage/pipeline_state.py (migration 008, issue #90)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.pipeline_state as ps


class TestPipelineState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        db.init_db()
        migrate.migrate_up()

    def tearDown(self):
        self._patch.stop()

    def test_get_default_when_absent(self):
        self.assertIsNone(ps.get_state("nope"))
        self.assertEqual(ps.get_state("nope", "fallback"), "fallback")
        self.assertEqual(ps.get_int("nope", 7), 7)

    def test_set_then_get(self):
        ps.set_state("k", "hello")
        self.assertEqual(ps.get_state("k"), "hello")

    def test_upsert_overwrites(self):
        ps.set_state("k", "one")
        ps.set_state("k", "two")
        self.assertEqual(ps.get_state("k"), "two")

    def test_int_roundtrip(self):
        ps.set_int("cursor", 1234)
        self.assertEqual(ps.get_int("cursor"), 1234)

    def test_get_int_corrupt_value_falls_back(self):
        ps.set_state("cursor", "not-a-number")
        self.assertEqual(ps.get_int("cursor", 42), 42)


if __name__ == "__main__":
    unittest.main()
