from __future__ import annotations

"""
Preview compression (Phase 5 EPIC #5.1 — Video compress <50MB).

Telegram Bot API từ chối file >50MB; trước đây video quá cỡ bị BỎ QUA và
reviewer chỉ duyệt qua script (issue #60). Module này nén một bản PREVIEW
riêng (hạ resolution + CRF cao) chỉ để gửi Telegram — file gốc dùng để upload
không bị đụng tới.

Nén 2 nấc: 720p/CRF28 trước; vẫn quá 50MB (video rất dài) thì 480p/CRF32.
Vẫn không lọt → trả None, caller giữ hành vi cũ (script-only review).
"""

import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from video.video_composer import _run_ffmpeg

logger = logging.getLogger(__name__)

# Trần của Telegram Bot API — đồng bộ với notifier.telegram_bot.TELEGRAM_MAX_FILE_BYTES.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024

# (max chiều-ngắn, CRF, audio bitrate) — thử lần lượt tới khi lọt size.
_PASSES = [
    (720, 28, "96k"),
    (480, 32, "64k"),
]


def build_preview_command(src: str, dst: str, short_side: int, crf: int,
                          audio_bitrate: str) -> list[str]:
    """ffmpeg command nén preview (pure, unit-testable).

    Scale theo chiều NGẮN (min(w,h) → short_side) để cả video dọc 1080x1920
    lẫn ngang 1920x1080 đều co về cùng cỡ; -2 giữ chẵn để libx264 không kêu.
    """
    scale = (f"scale='if(gt(iw,ih),-2,{short_side})'"
             f":'if(gt(iw,ih),{short_side},-2)'")
    return [
        "ffmpeg", "-y", "-i", src,
        "-vf", scale,
        "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        dst,
    ]


def compress_for_preview(video_path: str, preview_path: str | None = None,
                         max_bytes: int = DEFAULT_MAX_BYTES) -> str | None:
    """Trả về đường dẫn file gửi được qua Telegram (<= max_bytes).

    - File gốc đã đủ nhỏ → trả chính nó, không tốn ffmpeg.
    - Quá cỡ → nén ra `preview_path` (mặc định `<tên>_preview.mp4` cạnh file
      gốc) qua các nấc _PASSES. Không nấc nào lọt → None.
    """
    try:
        size = os.path.getsize(video_path)
    except OSError as e:
        logger.error("compress_for_preview: cannot stat %s: %s", video_path, e)
        return None
    if size <= max_bytes:
        return video_path

    if preview_path is None:
        base, _ = os.path.splitext(video_path)
        preview_path = f"{base}_preview.mp4"

    for short_side, crf, audio_bitrate in _PASSES:
        cmd = build_preview_command(video_path, preview_path, short_side, crf,
                                    audio_bitrate)
        if _run_ffmpeg(cmd, preview_path) is None:
            logger.error("Preview compression failed at %dp", short_side)
            return None
        new_size = os.path.getsize(preview_path)
        logger.info("Preview %dp/crf%d: %.1f MB → %.1f MB",
                    short_side, crf, size / 1024 / 1024, new_size / 1024 / 1024)
        if new_size <= max_bytes:
            return preview_path

    logger.error("Preview still over %d MB after all passes — giving up",
                 max_bytes // 1024 // 1024)
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        print(compress_for_preview(sys.argv[1]))
    else:
        print("Usage: python -m video.preview <video.mp4>")
