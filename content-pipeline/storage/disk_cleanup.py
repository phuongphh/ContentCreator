from __future__ import annotations

"""
Disk cleanup — dọn file video/audio/phụ đề cũ và cache clip nền Pexels.

Quy tắc:
- Video 'published' có published_at quá 30 ngày: xoá file video/audio/phụ đề (GIỮ bản ghi DB).
- Video 'rejected'/'failed' tạo quá 30 ngày: xoá file.
- Mọi video khác (chưa đăng): KHÔNG BAO GIỜ xoá.
- Cache Pexels chỉ giữ N clip dùng gần nhất (theo mtime).
- Chỉ xoá file nằm trong thư mục output / cache của dự án. Chỉ đọc DB (SELECT).

Chạy: python -m storage.disk_cleanup
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_connection

logger = logging.getLogger(__name__)

_FILE_COLUMNS = ("video_path", "audio_path", "subtitle_path")


def _parse_ts(value) -> datetime | None:
    """Parse SQLite 'YYYY-MM-DD HH:MM:SS[.fff]' (UTC) hoặc ISO; None nếu hỏng."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _is_inside(path: str, root: str) -> bool:
    """True nếu path (sau realpath) nằm trong root."""
    if not path or not root:
        return False
    try:
        real_path = os.path.realpath(path)
        real_root = os.path.realpath(root)
        return os.path.commonpath([real_path, real_root]) == real_root
    except ValueError:
        return False


def _delete_file(path: str, roots: list[str], stats: dict) -> None:
    """Xoá 1 file thường nếu nằm trong một root cho phép; cập nhật stats."""
    if not path:
        return
    if not any(_is_inside(path, r) for r in roots):
        logger.warning("Disk cleanup: skip path outside managed dirs: %s", path)
        return
    if not os.path.isfile(path):
        return
    try:
        size = os.path.getsize(path)
        os.remove(path)
    except OSError as e:
        logger.warning("Disk cleanup: could not delete %s: %s", path, e)
        return
    stats["files_deleted"] += 1
    stats["bytes_freed"] += size


def _cleanup_videos(now: datetime, output_dir: str, max_age_days: int,
                    stats: dict) -> None:
    cutoff = now - timedelta(days=max_age_days)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, status, created_at, published_at, "
            "audio_path, subtitle_path, video_path FROM videos"
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        status = row["status"]
        eligible = False
        if status == "published":
            ts = _parse_ts(row["published_at"])
            eligible = ts is not None and ts < cutoff
        elif status in ("rejected", "failed"):
            ts = _parse_ts(row["created_at"])
            eligible = ts is not None and ts < cutoff
        if not eligible:
            continue
        for col in _FILE_COLUMNS:
            _delete_file(row[col], [output_dir], stats)


def _prune_cache(cache_dir: str, ext: str, keep: int, stats: dict) -> None:
    if not cache_dir or not os.path.isdir(cache_dir):
        return
    files = []
    for name in os.listdir(cache_dir):
        if not name.endswith(ext):
            continue
        full = os.path.join(cache_dir, name)
        if os.path.isfile(full):
            try:
                files.append((os.path.getmtime(full), full))
            except OSError:
                continue
    files.sort(key=lambda x: x[0], reverse=True)
    for _mtime, full in files[max(0, keep):]:
        _delete_file(full, [cache_dir], stats)


def run_cleanup(now: datetime | None = None, output_dir: str | None = None,
                cache_dirs: list[tuple[str, str]] | None = None,
                max_age_days: int = 30, keep_clips: int = 50) -> dict:
    """Chạy một lượt dọn. Trả {files_deleted, bytes_freed}."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if output_dir is None:
        output_dir = config.VIDEO_OUTPUT_DIR
    if cache_dirs is None:
        import video.pexels_downloader as pex
        cache_dirs = [(pex.CACHE_DIR, ".mp4"), (pex.PHOTO_CACHE_DIR, ".jpg")]

    stats = {"files_deleted": 0, "bytes_freed": 0}
    try:
        _cleanup_videos(now, output_dir, max_age_days, stats)
    except Exception as e:
        logger.error("Disk cleanup: video cleanup failed: %s", e)
    for cache_dir, ext in cache_dirs:
        try:
            _prune_cache(cache_dir, ext, keep_clips, stats)
        except Exception as e:
            logger.error("Disk cleanup: cache prune failed for %s: %s", cache_dir, e)

    logger.info("Disk cleanup: deleted %d files, freed %d bytes",
                stats["files_deleted"], stats["bytes_freed"])
    return stats


def main():
    logging.basicConfig(level=logging.INFO)
    run_cleanup()


if __name__ == "__main__":
    main()
