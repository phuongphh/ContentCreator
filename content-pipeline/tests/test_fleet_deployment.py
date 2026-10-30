import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "fleet_product_adapter", ROOT / "ops/fleet/verify_dev.py"
)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

import sqlite3


class DeploymentTests(unittest.TestCase):
    def test_db_must_have_real_application_tables_and_never_implies_publishing_guard(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "dev.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE videos (id INTEGER)")
            with self.assertRaises(ValueError):
                adapter.verify(db)
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE stories (id INTEGER)")
                conn.execute("CREATE TABLE scheduled_posts (id INTEGER)")
            before = db.read_bytes()
            result = adapter.verify(db)
            self.assertTrue(result["database"])
            self.assertFalse(result["ready_for_uat"])
            self.assertFalse(result["publishing_guard"])
            self.assertEqual(db.read_bytes(), before)
