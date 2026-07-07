from __future__ import annotations

"""
TikTok Manual Export Queue (Phase 5 EPIC #5.3 — giai đoạn 1).

Copy video + file .txt chứa caption/hashtag vào `queue_tiktok/YYYY-MM-DD/`
để Phuong mở thư mục mỗi sáng, upload tay 5-10 phút — không phụ thuộc
TikTok Content Posting API (approval mất 2-4 tuần). Khi API sẵn sàng
(TIKTOK_ACCESS_TOKEN có giá trị), scheduler sẽ dùng
publisher/tiktok_uploader.py thay vì queue này.

Idempotent: export lại cùng video chỉ ghi đè đúng file của nó (tên file có
video id), không tạo bản sao mới.
"""

import logging
import os
import shutil
from datetime import date

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_video

logger = logging.getLogger(__name__)


def export_for_manual_upload(video_id: int, queue_dir: str | None = None) -> str | None:
    """Copy video + caption vào thư mục queue theo ngày. Returns đường dẫn MP4.

    Cấu trúc output:
        queue_tiktok/2026-07-07/video_12.mp4
        queue_tiktok/2026-07-07/video_12.txt   (caption + hashtags, copy-paste nhanh)
    """
    video = get_video(video_id)
    if not video:
        logger.error("export_for_manual_upload: video %d not found", video_id)
        return None
    video_path = video.get("video_path")
    if not video_path or not os.path.exists(video_path):
        logger.error("export_for_manual_upload: file missing for video %d: %s",
                     video_id, video_path)
        return None

    base_dir = queue_dir or config.TIKTOK_QUEUE_DIR
    day_dir = os.path.join(base_dir, date.today().isoformat())
    os.makedirs(day_dir, exist_ok=True)

    dest_mp4 = os.path.join(day_dir, f"video_{video_id}.mp4")
    shutil.copy2(video_path, dest_mp4)

    caption = video.get("tiktok_caption", "") or video.get("youtube_title", "")
    hashtags = video.get("tiktok_hashtags", "") or ""
    lines = [caption.strip()]
    if hashtags.strip():
        lines += ["", hashtags.strip()]
    with open(os.path.join(day_dir, f"video_{video_id}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info("Exported video %d to TikTok manual queue: %s", video_id, dest_mp4)
    return dest_mp4


def list_queue(queue_dir: str | None = None) -> dict[str, list[str]]:
    """{ 'YYYY-MM-DD': [file, ...] } — các video đang chờ upload tay."""
    base_dir = queue_dir or config.TIKTOK_QUEUE_DIR
    if not os.path.isdir(base_dir):
        return {}
    result = {}
    for day in sorted(os.listdir(base_dir)):
        day_dir = os.path.join(base_dir, day)
        if os.path.isdir(day_dir):
            mp4s = sorted(f for f in os.listdir(day_dir) if f.endswith(".mp4"))
            if mp4s:
                result[day] = mp4s
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for day, files in list_queue().items():
        print(f"{day}: {len(files)} video → {', '.join(files)}")
