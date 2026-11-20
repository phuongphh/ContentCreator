from __future__ import annotations

import fcntl
import os
import plistlib
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
from storage import disk_cleanup_job


def _ts(days_ago: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class DiskCleanupJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.output = os.path.join(self.tmp, "output")
        os.makedirs(self.output)
        self.lock = os.path.join(self.tmp, "job.lock")
        p = patch.object(config, "DB_PATH", os.path.join(self.tmp, "t.db"))
        p.start()
        self.addCleanup(p.stop)
        database.init_db()
        # Không đụng cache Pexels thật
        real = disk_cleanup.run_cleanup
        p2 = patch.object(
            disk_cleanup, "run_cleanup",
            side_effect=lambda **kw: real(cache_dirs=[], **kw))
        self.mock_run = p2.start()
        self.addCleanup(p2.stop)

    def _args(self, output=None):
        return ["--output-dir", output or self.output,
                "--lock-file", self.lock]

    def _old_video(self):
        paths = []
        for ext in ("mp4", "mp3", "srt"):
            path = os.path.join(self.output, f"v.{ext}")
            with open(path, "wb") as f:
                f.write(b"x" * 100)
            paths.append(path)
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO videos (video_type, script_text, video_path, audio_path, "
            "subtitle_path, status, created_at, published_at) VALUES "
            "('short','s',?,?,?,'published',?,?)",
            (paths[0], paths[1], paths[2], _ts(40), _ts(31)))
        conn.commit()
        conn.close()
        return paths

    def test_success_exit_zero(self):
        self.assertEqual(disk_cleanup_job.main(self._args()), 0)

    def test_missing_dir_nonzero(self):
        rc = disk_cleanup_job.main(self._args(os.path.join(self.tmp, "nope")))
        self.assertNotEqual(rc, 0)

    def test_run_cleanup_error_nonzero(self):
        self.mock_run.side_effect = RuntimeError("boom")
        self.assertEqual(disk_cleanup_job.main(self._args()), 1)

    def test_log_line_counts(self):
        self._old_video()
        with self.assertLogs("storage.disk_cleanup_job", level="INFO") as cm:
            rc = disk_cleanup_job.main(self._args())
        self.assertEqual(rc, 0)
        self.assertTrue(any("files_deleted=3" in m and "bytes_freed=300" in m
                            and "at=" in m for m in cm.output))

    def test_lock_held_skips(self):
        paths = self._old_video()
        fh = open(self.lock, "a+")
        self.addCleanup(fh.close)
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = disk_cleanup_job.main(self._args())
        self.assertEqual(rc, 3)
        self.mock_run.assert_not_called()
        for p in paths:
            self.assertTrue(os.path.exists(p))

    def test_plist(self):
        path = os.path.join(os.path.dirname(__file__), "..", "launchd",
                            "com.ai5phut.disk-cleanup.plist")
        with open(path, "rb") as f:
            data = plistlib.load(f)
        self.assertEqual(data["Label"], "com.ai5phut.disk-cleanup")
        self.assertTrue(data["Label"].startswith("com.ai5phut."))
        self.assertEqual(data["StartCalendarInterval"]["Hour"], 23)
        self.assertEqual(data["StartCalendarInterval"]["Minute"], 30)
        self.assertIn("storage.disk_cleanup_job", data["ProgramArguments"])


if __name__ == "__main__":
    unittest.main()
