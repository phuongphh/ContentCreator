from __future__ import annotations

"""
TTS Client — Wrapper cho Núi Trúc TTS API.

API: http://tts.nuitruc.ai/api/tts
- POST JSON: {"text": "...", "voice_id": "voice1", "speed": 1.0}
- Output: WAV (Content-Type: audio/wav)
- Timeout: 5 phút (300s) — gửi full article text trong 1 request
"""

import json
import logging
import os
import re
import ssl
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

TTS_TIMEOUT = 300  # 5 phút — full article text trong 1 request
TTS_MAX_RETRIES = 3         # Số lần retry khi gặp lỗi tạm thời (503, 500, timeout)
TTS_RETRY_DELAY = 5         # Giây chờ ban đầu giữa các retry (exponential backoff)
TTS_CHUNK_MAX_CHARS = 700   # Ký tự tối đa mỗi chunk (API trả 504 nếu quá dài)


def _split_text_into_chunks(text: str, max_chars: int = TTS_CHUNK_MAX_CHARS) -> list[str]:
    """Split text into chunks at sentence boundaries, each ≤ max_chars.

    Splits at '. ' before an uppercase letter or '.\\n'. Falls back to
    comma splitting for individual sentences that still exceed max_chars.
    """
    raw_sentences = re.split(r'(?<=\.)\s+', text)
    sentences = [s.strip() for s in raw_sentences if s.strip()]

    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            # Flush current buffer first
            if current:
                chunks.append(current.strip())
                current = ""
            # Split oversized sentence at ", " boundaries
            sub_parts = sentence.split(", ")
            sub_chunk = ""
            for part in sub_parts:
                candidate = sub_chunk + ", " + part if sub_chunk else part
                if len(candidate) <= max_chars:
                    sub_chunk = candidate
                else:
                    if sub_chunk:
                        chunks.append(sub_chunk.strip())
                    sub_chunk = part
            if sub_chunk:
                chunks.append(sub_chunk.strip())
        else:
            candidate = current + " " + sentence if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current.strip())
                current = sentence

    if current:
        chunks.append(current.strip())

    return chunks if chunks else [text]


def text_to_speech(text: str, output_path: str) -> str | None:
    """Convert text to speech audio file.

    Gửi full article text trong 1 request duy nhất tới TTS API.

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

    chunks = _split_text_into_chunks(text)
    if len(chunks) == 1:
        return _tts_single(text, output_path)

    # Multiple chunks — generate each to temp WAV files, then concatenate
    logger.info("TTS: splitting %d chars into %d chunks", len(text), len(chunks))
    tmp_dir = tempfile.mkdtemp(prefix="tts_chunks_")
    chunk_paths: list[str] = []
    list_file = os.path.join(tmp_dir, "concat_list.txt")
    try:
        for i, chunk in enumerate(chunks):
            chunk_path = os.path.join(tmp_dir, f"chunk_{i:03d}.wav")
            result = _tts_single(chunk, chunk_path)
            if result is None:
                logger.error("TTS failed for chunk %d/%d, aborting", i + 1, len(chunks))
                return None
            chunk_paths.append(chunk_path)
            logger.info("TTS chunk %d/%d done (%d chars)", i + 1, len(chunks), len(chunk))

        with open(list_file, "w") as f:
            for cp in chunk_paths:
                f.write(f"file '{cp}'\n")

        concat_result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-c", "copy", output_path],
            capture_output=True, text=True, timeout=120,
        )
        if concat_result.returncode != 0:
            logger.error("ffmpeg concat failed: %s", concat_result.stderr)
            return None

        logger.info("TTS chunks concatenated -> %s", output_path)
        return output_path

    finally:
        for cp in chunk_paths:
            try:
                os.remove(cp)
            except OSError:
                pass
        try:
            os.remove(list_file)
        except OSError:
            pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
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
    # Test ffprobe
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=5)
        print("ffprobe: OK")
    except FileNotFoundError:
        print("ffprobe: NOT FOUND — brew install ffmpeg")
