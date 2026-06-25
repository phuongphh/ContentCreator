from __future__ import annotations

"""
Audio Mixer — Trộn nhạc nền (BGM) dưới giọng đọc với ducking (P1).

Giọng đọc luôn là chủ đạo: nhạc nền bị hạ âm lượng (BGM_VOLUME_DB) và tự giảm
thêm khi có giọng (sidechain compress / "ducking"). Nhạc được loop để phủ hết
độ dài giọng và cắt theo giọng (-shortest).

Chỉ chạy khi config.ENABLE_BGM=1. Thiếu nhạc / lỗi → trả lại audio giọng gốc,
không làm pipeline chết.
"""

import logging
import os
import random

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from video.video_composer import _run_ffmpeg

logger = logging.getLogger(__name__)

_MUSIC_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg")


def pick_music(music_dir: str | None = None) -> str | None:
    """Return a random royalty-free music file from the music dir, or None."""
    music_dir = music_dir or getattr(config, "MUSIC_DIR", "")
    if not music_dir or not os.path.isdir(music_dir):
        return None
    tracks = [
        os.path.join(music_dir, f)
        for f in sorted(os.listdir(music_dir))
        if f.lower().endswith(_MUSIC_EXTS)
    ]
    if not tracks:
        return None
    return random.choice(tracks)


def build_mix_command(voice_path: str, music_path: str, output_path: str,
                      music_db: float = -18.0) -> list[str]:
    """Build the ffmpeg command that mixes music under voice with ducking (pure).

    - voice = input 0 (control / sidechain key), music = input 1.
    - Music is looped (-stream_loop) and attenuated by ``music_db`` dB, then
      sidechain-compressed by the voice so it dips when narration is present.
    - Output length follows the voice (-shortest).
    """
    return [
        "ffmpeg", "-y",
        "-i", voice_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex",
        (
            f"[1:a]volume={music_db}dB[bg];"
            "[bg][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=5:release=300[ducked];"
            "[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=0[a]"
        ),
        "-map", "[a]",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]


def mix_background_music(voice_path: str, output_path: str,
                         music_path: str | None = None,
                         music_db: float | None = None) -> str:
    """Mix BGM under the narration. Returns the mixed path, or voice on failure.

    Always returns a usable audio path so callers can use it unconditionally.
    """
    if music_path is None:
        music_path = pick_music()
    if not music_path or not os.path.exists(music_path):
        logger.info("No background music available — using narration only")
        return voice_path
    if music_db is None:
        music_db = getattr(config, "BGM_VOLUME_DB", -18.0)

    cmd = build_mix_command(voice_path, music_path, output_path, music_db)
    result = _run_ffmpeg(cmd, output_path)
    if result is None:
        logger.warning("BGM mix failed — using narration only")
        return voice_path
    logger.info("Mixed BGM '%s' under narration", os.path.basename(music_path))
    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Music dir: {config.MUSIC_DIR}")
    print(f"Picked: {pick_music()}")
