"""Tests cho log rotation của run_module.sh (issue #107 — bot_stdout.log 3.4GB).

Wrapper rotate lúc spawn: file log vượt LOG_MAX_BYTES → giữ ĐUÔI mới nhất sang
<file>.1 rồi làm rỗng file chính. Dùng tail -c thay vì mv nguyên file để một
file đã phình khổng lồ co ngay về trần trong một lần rotate.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "run_module.sh"))


def _run_wrapper(log_dir: str, max_bytes: int) -> subprocess.CompletedProcess:
    env = dict(os.environ, LOG_DIR=log_dir, LOG_BASENAME="testlog",
               LOG_MAX_BYTES=str(max_bytes))
    # Môi trường test thường không có venv → wrapper exit 78 SAU bước rotate;
    # ta chỉ kiểm tra hiệu ứng rotation trên file, không cần exec python thật.
    return subprocess.run(["bash", SCRIPT, "-c", "pass"], env=env,
                          capture_output=True, timeout=30)


class TestLogRotation(unittest.TestCase):
    def test_oversized_log_rotated_keeping_newest_tail(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "testlog_stdout.log")
            with open(f, "wb") as fh:
                fh.write(b"A" * 900 + b"TAIL_MARKER")
            _run_wrapper(d, 500)
            # File chính đã co về ~0 (chỉ còn log mới của chính lần chạy này).
            self.assertLess(os.path.getsize(f), 900)
            with open(f + ".1", "rb") as fh:
                kept = fh.read()
            # .1 giữ đúng ĐUÔI mới nhất, bị cap ở LOG_MAX_BYTES.
            self.assertEqual(len(kept), 500)
            self.assertTrue(kept.endswith(b"TAIL_MARKER"))

    def test_small_log_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "testlog_stdout.log")
            with open(f, "wb") as fh:
                fh.write(b"small-but-precious")
            _run_wrapper(d, 500)
            self.assertFalse(os.path.exists(f + ".1"))
            with open(f, "rb") as fh:
                self.assertTrue(fh.read().startswith(b"small-but-precious"))

    def test_missing_log_files_ok(self):
        # Lần chạy đầu tiên (chưa có file log nào) không được vỡ vì rotation.
        with tempfile.TemporaryDirectory() as d:
            proc = _run_wrapper(d, 500)
            # Wrapper đi tiếp qua bước rotate (venv thiếu → 78, venv có → mã
            # của lệnh python) — miễn không phải lỗi bash cấp cú pháp/rotate.
            self.assertIn(proc.returncode, (0, 78))


if __name__ == "__main__":
    unittest.main()
