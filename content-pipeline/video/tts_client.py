from __future__ import annotations

"""
TTS Client — Wrapper cho Núi Trúc TTS API.

API: http://tts.nuitruc.ai/api/tts
- POST JSON: {"text": "...", "voice_id": "voice1", "speed": 1.0}
- Output: WAV (Content-Type: audio/wav)
- Gateway timeout: ~90s per request

Long scripts are split into ≤700-char sentence-boundary chunks and processed
sequentially. Each chunk is retried up to 3 times before the whole call fails.
Chunks are concatenated into one MP3 via ffmpeg.
"""

import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

TTS_TIMEOUT = 90            # 90s — matches the gateway limit; no point setting longer
MAX_CHARS_PER_CHUNK = 700   # Stay well under what the gateway can process in 90s
TTS_MAX_RETRIES = 3         # Retries per chunk for transient errors (503, 500, timeout)
TTS_RETRY_DELAY = 5         # Initial wait (seconds); doubles each retry


def text_to_speech(text: str, output_path: str) -> str | None:
    """Convert text to speech audio file.

    Splits long scripts into ≤700-char chunks at sentence boundaries, calls
    the TTS API sequentially for each chunk, then concatenates with ffmpeg.

    Args:
        text: Script text to convert.
        output_path: Path to save final audio file (.mp3).

    Returns:
        Path to the audio file, or None on failure.
    """
    if not config.TTS_API_URL:
        logger.error("TTS_API_URL not configured")
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    chunks = _split_text(text)
    if len(chunks) == 1:
        return _tts_single(chunks[0], output_path)

    # Multiple chunks — process sequentially, then concat
    logger.info("Text split into %d chunks for TTS", len(chunks))
    chunk_dir = output_path + "_chunks"
    os.makedirs(chunk_dir, exist_ok=True)

    chunk_paths: list[str] = []
    for i, chunk in enumerate(chunks):
        chunk_path = os.path.join(chunk_dir, f"chunk_{i:03d}.wav")
        result = _tts_single(chunk, chunk_path)
        if result is None:
            logger.error("TTS failed for chunk %d — aborting", i)
            _cleanup_dir(chunk_dir)
            return None
        chunk_paths.append(result)

    result = _concat_audio(chunk_paths, output_path)
    _cleanup_dir(chunk_dir)
    return result


def _split_text(text: str) -> list[str]:
    """Split text into chunks of ≤MAX_CHARS_PER_CHUNK at sentence boundaries.

    Cuts at Vietnamese/English sentence-ending punctuation (., !, ?) to keep
    audio natural across chunk boundaries.
    """
    if len(text) <= MAX_CHARS_PER_CHUNK:
        return [text]

    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 > MAX_CHARS_PER_CHUNK and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _concat_audio(paths: list[str], output_path: str) -> str | None:
    """Concatenate multiple WAV chunk files into one MP3 via ffmpeg."""
    list_path = output_path + ".txt"
    try:
        with open(list_path, "w") as f:
            for p in paths:
                f.write(f"file '{p}'\n")

        # TTS API returns WAV; let ffmpeg transcode to MP3 (do NOT use -c copy
        # when mixing WAV input with MP3 output container).
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error("ffmpeg concat failed: %s", result.stderr[-500:])
            return None

        size_kb = os.path.getsize(output_path) / 1024
        logger.info("Audio concatenated: %s (%.1f KB)", output_path, size_kb)
        return output_path

    except Exception as e:
        logger.error("Audio concat failed: %s", e)
        return None
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)


def _cleanup_dir(dir_path: str) -> None:
    """Remove temporary chunk directory."""
    try:
        shutil.rmtree(dir_path, ignore_errors=True)
    except Exception:
        pass


def _build_opener_with_ssl() -> object:
    """Build a urllib opener that uses a permissive SSL context for all HTTPS requests.

    Using build_opener ensures the permissive context is also applied to any
    HTTP→HTTPS redirects, not just the initial request.
    """
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    return build_opener(HTTPSHandler(context=ssl_ctx))


def _is_transient_error(exc: Exception) -> bool:
    """Return True for errors worth retrying (server down, overloaded, timeout)."""
    if isinstance(exc, HTTPError):
        return exc.code in (429, 500, 502, 503, 504)
    if isinstance(exc, (TimeoutError, ssl.SSLError)):
        return True
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        return isinstance(reason, (TimeoutError, ssl.SSLError))
    return False


def _tts_single(text: str, output_path: str) -> str | None:
    """Call TTS API for a single text chunk with retry + SSL fallback."""
    payload = json.dumps({
        "text": text,
        "voice_id": config.TTS_VOICE_ID or "voice1",
        "speed": config.TTS_VOICE_SPEED,
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if config.TTS_API_KEY:
        headers["Authorization"] = f"Bearer {config.TTS_API_KEY}"

    url = config.TTS_API_URL

    # Build opener with permissive SSL context so it also applies to HTTP→HTTPS
    # redirects (urllib doesn't reuse a custom ssl context across redirects by default).
    opener = _build_opener_with_ssl()

    last_exc: Exception | None = None
    for attempt in range(1, TTS_MAX_RETRIES + 1):
        try:
            req = Request(url, data=payload, headers=headers)
            with opener.open(req, timeout=TTS_TIMEOUT) as resp:
                with open(output_path, "wb") as f:
                    f.write(resp.read())

            size_kb = os.path.getsize(output_path) / 1024
            logger.info("TTS chunk saved: %s (%.1f KB)", output_path, size_kb)
            return output_path

        except (ssl.SSLError, URLError, HTTPError, OSError) as e:
            last_exc = e
            if _is_transient_error(e) and attempt < TTS_MAX_RETRIES:
                wait = TTS_RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning("TTS attempt %d/%d failed (%s), retrying in %ds...",
                               attempt, TTS_MAX_RETRIES, e, wait)
                time.sleep(wait)
                continue
            # Non-transient or last attempt — break out and report
            break

        except Exception as e:
            last_exc = e
            break

    logger.error("TTS API failed: %s", last_exc)
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
    print(f"TTS endpoint: {config.TTS_API_URL or '(not set)'}")
    print(f"TTS voice: {config.TTS_VOICE_ID or '(not set)'}")
    print(f"TTS speed: {config.TTS_VOICE_SPEED}")
    print(f"Max chars per chunk: {MAX_CHARS_PER_CHUNK}")
    # Test ffprobe
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=5)
        print("ffprobe: OK")
    except FileNotFoundError:
        print("ffprobe: NOT FOUND — brew install ffmpeg")
