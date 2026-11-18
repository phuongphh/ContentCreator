from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from storage import database
from storage import disk_cleanup


def _ts(days_ago: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class DiskCleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.output = os.path.join(self.tmp, "output")
        os.makedirs(self.output)
        self.cache = os.path.join(self.tmp, "cache")
        os.makedirs(self.cache)
        p = patch.object(config, "DB_PATH", os.path.join(self.tmp, "t.db"))
        p.start()
        self.addCleanup(p.stop)
        database.init_db()

    def _video(self, status, created_days, published_days=None, outside=False):
        base = self.tmp if outside else self.output
        paths = []
        for ext in ("mp4", "mp3", "srt"):
            path = os.path.join(base, f"v_{status}_{created_days}_{ext}.{ext}")
            with open(path, "wb") as f:
                f.write(b"x" * 100)
            paths.append(path)
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO videos (video_type, script_text, video_path, audio_path, "
            "subtitle_path, status, created_at, published_at) VALUES "
            "('short','s',?,?,?,?,?,?)",
            (paths[0], paths[1], paths[2], status, _ts(created_days),
             _ts(published_days) if published_days is not None else None),
        )
        conn.commit()
        conn.close()
        return paths

    def _run(self):
        return disk_cleanup.run_cleanup(output_dir=self.output,
                                        cache_dirs=[(self.cache, ".mp4")])

    def test_published_old_deleted_row_kept(self):
        paths = self._video("published", 40, published_days=31)
        self._run()
        for p in paths:
            self.assertFalse(os.path.exists(p))
        conn = database.get_connection()
        n = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    def test_unpublished_never_deleted(self):
        paths = self._video("approved", 200)
        self._run()
        for p in paths:
            self.assertTrue(os.path.exists(p))

    def test_rejected_failed_age(self):
        old = self._video("rejected", 31) + self._video("failed", 31)
        new = self._video("rejected", 29) + self._video("failed", 29)
        self._run()
        for p in old:
            self.assertFalse(os.path.exists(p))
        for p in new:
            self.assertTrue(os.path.exists(p))

    def test_cache_keeps_50_newest(self):
        now = datetime.now().timestamp()
        files = []
        for i in range(60):
            p = os.path.join(self.cache, f"clip_{i:02d}.mp4")
            with open(p, "wb") as f:
                f.write(b"x")
            os.utime(p, (now - (60 - i) * 100, now - (60 - i) * 100))
            files.append(p)
        self._run()
        for p in files[:10]:
            self.assertFalse(os.path.exists(p))
        for p in files[10:]:
            self.assertTrue(os.path.exists(p))

    def test_log_counts_and_bytes(self):
        self._video("published", 40, published_days=31)
        with self.assertLogs("storage.disk_cleanup", level="INFO") as cm:
            self._run()
        self.assertTrue(any("deleted 3 files, freed 300 bytes" in m
                            for m in cm.output))

    def test_outside_files_untouched(self):
        decoy = os.path.join(self.tmp, "decoy.txt")
        with open(decoy, "w") as f:
            f.write("keep")
        outside = self._video("published", 40, published_days=31, outside=True)
        self._run()
        self.assertTrue(os.path.exists(decoy))
        for p in outside:
            self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
