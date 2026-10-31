import importlib.util
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "fleet_product_adapter", ROOT / "ops/fleet/verify_dev.py"
)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class DeploymentTests(unittest.TestCase):
    def test_three_id_only_tables_are_not_a_working_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "dev.sqlite3"
            with sqlite3.connect(db) as connection:
                for table in ["videos", "stories", "scheduled_posts"]:
                    connection.execute("CREATE TABLE " + table + " (id INTEGER)")
            with self.assertRaises(ValueError):
                adapter.verify(db)

    def test_real_migrations_are_required_and_verification_does_not_write(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            adapter.reference_database() as reference,
        ):
            db = Path(temporary) / "dev.sqlite3"
            shutil.copyfile(reference, db)
            before = db.read_bytes()
            result = adapter.verify(db)
            self.assertTrue(result["database"])
            self.assertGreaterEqual(result["migrations_verified"], 10)
            self.assertFalse(result["ready_for_uat"])
            self.assertFalse(result["publishing_guard"])
            self.assertEqual(db.read_bytes(), before)
            with sqlite3.connect(db) as connection:
                connection.execute(
                    "DELETE FROM _migrations WHERE version='010_stories_content_hash'"
                )
            with self.assertRaises(ValueError):
                adapter.verify(db)

    def test_claimed_versions_cannot_hide_a_missing_column_or_index(self):
        for sql in [
            "ALTER TABLE stories RENAME COLUMN content_hash TO missing_content_hash",
            "DROP INDEX ux_video_metrics_snapshot",
        ]:
            with (
                self.subTest(sql=sql),
                tempfile.TemporaryDirectory() as temporary,
                adapter.reference_database() as reference,
            ):
                db = Path(temporary) / "dev.sqlite3"
                shutil.copyfile(reference, db)
                with sqlite3.connect(db) as connection:
                    connection.execute(sql)
                with self.assertRaises(ValueError):
                    adapter.verify(db)
