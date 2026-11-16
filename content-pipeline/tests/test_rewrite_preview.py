"""Tests for the Dev rewrite preview (canned replies, temp DB, no network)."""
import hashlib
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]


class RewritePreviewTests(unittest.TestCase):
    def test_cases_status_no_network_and_db_untouched(self):
        import config
        import storage.migrate as migrate
        from webui import rewrite_preview

        with tempfile.TemporaryDirectory() as tmp:
            dev_db = os.path.join(tmp, "dev.db")
            old = config.DB_PATH
            config.DB_PATH = dev_db
            try:
                migrate.migrate_up()
                digest = hashlib.sha256(Path(dev_db).read_bytes()).hexdigest()
                mtime = os.stat(dev_db).st_mtime_ns

                def boom(*a, **k):
                    raise AssertionError("network used")

                with patch.object(socket.socket, "connect", boom), \
                        patch("anthropic.Anthropic", boom):
                    results = rewrite_preview.run_preview()

                self.assertEqual(config.DB_PATH, dev_db)
                self.assertEqual(hashlib.sha256(Path(dev_db).read_bytes()).hexdigest(), digest)
                self.assertEqual(os.stat(dev_db).st_mtime_ns, mtime)
            finally:
                config.DB_PATH = old

        by_name = {r["name"]: r for r in results}
        self.assertEqual(len(results), 5)
        self.assertEqual(by_name["Hợp lệ (kể chuyện)"]["status"], "approved")
        trunc = by_name["Bị cắt cụt ở max_tokens"]
        self.assertEqual(trunc["status"], "needs_review")
        self.assertIn("max_tokens", trunc["reason"])
        self.assertEqual(by_name["Thiếu trường, kết thúc bình thường"]["status"], "needs_review")
        self.assertIn("missing", by_name["Thiếu trường, kết thúc bình thường"]["reason"])
        self.assertEqual(by_name["Hook quá dài rồi hợp lệ khi sinh lại"]["status"], "approved")
        self.assertEqual(by_name["Hội thoại có mảng dialogue hợp lệ"]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
