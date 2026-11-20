from __future__ import annotations

"""
Entrypoint chạy dọn ổ đĩa hằng ngày (launchd) — bọc storage.disk_cleanup.run_cleanup.

Chạy: python -m storage.disk_cleanup_job [--output-dir DIR] [--lock-file FILE]

Mã thoát: 0 thành công; 1 lỗi khi dọn; 2 thư mục output không tồn tại;
3 đã có lượt khác đang chạy (khoá). Không đổi quy tắc dọn của #161.
"""

import argparse
import fcntl
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage import disk_cleanup

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BAD_DIR = 2
EXIT_LOCKED = 3

_DEFAULT_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".disk_cleanup.lock")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Daily disk cleanup job")
    parser.add_argument("--output-dir",
                        default=os.environ.get("DISK_CLEANUP_OUTPUT_DIR"))
    parser.add_argument("--lock-file",
                        default=os.environ.get("DISK_CLEANUP_LOCK_FILE", _DEFAULT_LOCK))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    output_dir = args.output_dir or config.VIDEO_OUTPUT_DIR

    try:
        lock_fh = open(args.lock_file, "a+")
    except OSError as e:
        logger.error("Disk cleanup: cannot open lock file %s: %s", args.lock_file, e)
        return EXIT_ERROR
    try:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.warning("Disk cleanup already running, skipping")
            return EXIT_LOCKED

        if not os.path.isdir(output_dir):
            logger.error("Disk cleanup: output dir does not exist: %s", output_dir)
            return EXIT_BAD_DIR

        try:
            stats = disk_cleanup.run_cleanup(output_dir=output_dir)
        except Exception as e:
            logger.error("Disk cleanup failed: %s", e)
            return EXIT_ERROR

        logger.info("Disk cleanup run: at=%s files_deleted=%d bytes_freed=%d",
                    datetime.now(timezone.utc).isoformat(),
                    stats["files_deleted"], stats["bytes_freed"])
        return EXIT_OK
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        finally:
            lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
