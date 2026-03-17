"""
TTS Client — Abstract wrapper cho Text-to-Speech API.

Hỗ trợ:
- OpenAI TTS API (mặc định)
- Dễ mở rộng cho ElevenLabs, Google TTS, v.v.

Trả về file audio (.mp3) và duration (giây).
"""

import json
import logging
import os
import subprocess
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)


def text_to_speech(text: str, output_path: str) -> str | None:
    """Convert text to speech audio file.

    Args:
        text: Script text to convert.
        output_path: Path to save the audio file (.mp3).

    Returns:
        Path to the audio file, or None on failure.
    """
    if not config.TTS_API_URL or not config.TTS_API_KEY:
        logger.error("TTS_API_URL or TTS_API_KEY not configured")
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        payload = json.dumps({
            "model": "tts-1",
            "input": text,
            "voice": config.TTS_VOICE_ID or "alloy",
            "response_format": "mp3",
        }).encode("utf-8")

        req = Request(
            config.TTS_API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {config.TTS_API_KEY}",
                "Content-Type": "application/json",
            },
        )

        with urlopen(req, timeout=120) as resp:
            with open(output_path, "wb") as f:
                f.write(resp.read())

        logger.info("TTS audio saved: %s", output_path)
        return output_path

    except Exception as e:
        logger.error("TTS API failed: %s", e)
        return None


def get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.error("ffprobe failed for %s: %s", audio_path, e)
        return 0.0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("TTS client ready. Configure TTS_API_URL and TTS_API_KEY in .env")
    # Test ffprobe availability
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=5)
        print("ffprobe: OK")
    except FileNotFoundError:
        print("ffprobe: NOT FOUND — install ffmpeg first")
