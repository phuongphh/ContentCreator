from __future__ import annotations

"""
TTS Client — Wrapper cho Núi Trúc TTS API.

API: http://tts.nuitruc.ai/api/tts
- POST JSON: {"text": "...", "voice_id": "voice1", "speed": 1.0}
- Output: WAV (Content-Type: audio/wav)
- Long scripts are split into ~700-char chunks to avoid 504 Gateway Timeout.
"""

import json
import logging
import os
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

TTS_TIMEOUT = 120  # 2 phút per chunk
TTS_MAX_RETRIES = 3         # Số lần retry khi gặp lỗi tạm thời (503, 500, timeout)
TTS_RETRY_DELAY = 5         # Giây chờ ban đầu giữa các retry (exponential backoff)
TTS_CHUNK_MAX_CHARS = 700   # Giới hạn ký tự mỗi chunk để tránh 504


def _split_text_into_chunks(text: str, max_chars: int = TTS_CHUNK_MAX_CHARS) -> list[str]:
    """Split text into chunks of at most max_chars, preferring sentence boundaries.

    Splits at ". " or period followed by newline. If a sentence exceeds max_chars,
    falls back to splitting at ", " (comma boundaries).
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text.strip()

    while len(remaining) > max_chars:
        # Try to find a sentence boundary within the limit
        split_pos = -1
        search_window = remaining[:max_chars + 1]

        # Search backwards for ". " or ".\n" within the window
        for i in range(len(search_window) - 1, -1, -1):
            if search_window[i] == '.' and i + 1 < len(search_window) and search_window[i + 1] in (' ', '\n'):
                split_pos = i + 2  # include the period, skip the space/newline
                break

        if split_pos <= 0:
            # Fall back to comma boundary
            for i in range(len(search_window) - 1, -1, -1):
                if search_window[i] == ',' and i + 1 < len(search_window) and search_window[i + 1] == ' ':
                    split_pos = i + 2
                    break

        if split_pos <= 0:
            # Hard split at max_chars as last resort
            split_pos = max_chars

        chunk = remaining[:split_pos].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_pos:].strip()

    if remaining:
        chunks.append(remaining)

    logger.info("Split text (%d chars) into %d chunk(s)", len(text), len(chunks))
    return chunks


def text_to_speech(text: str, output_path: str) -> str | None:
    """Convert text to speech audio file.

    Splits long text into ~700-char chunks, calls TTS API per chunk,
    then concatenates the WAV files with ffmpeg if needed.

    Args:
        text: Script text to convert.
        output_path: Path to save final audio file.

    Returns:
        Path to the audio file, or None on failure.
    """
    if not config.TTS_API_URL:
        logger.error("TTS_API_URL not configured")
        return None

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    chunks = _split_text_into_chunks(text)

    if len(chunks) == 1:
        return _tts_single(chunks[0], output_path)

    # Multiple chunks — write each to a temp file, then concatenate
    tmp_dir = tempfile.mkdtemp(prefix="tts_chunks_")
    chunk_paths: list[str] = []
    try:
        for idx, chunk in enumerate(chunks):
            chunk_path = os.path.join(tmp_dir, f"chunk_{idx:03d}.wav")
            result = _tts_single(chunk, chunk_path)
            if result is None:
                logger.error("TTS failed on chunk %d/%d — aborting", idx + 1, len(chunks))
                return None
            chunk_paths.append(chunk_path)

        # Write ffmpeg concat filelist
        filelist_path = os.path.join(tmp_dir, "filelist.txt")
        with open(filelist_path, "w", encoding="utf-8") as f:
            for p in chunk_paths:
                f.write(f"file '{p}'\n")

        logger.info("Concatenating %d chunks -> %s", len(chunk_paths), output_path)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", filelist_path, "-c", "copy", output_path],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            logger.error("ffmpeg concat failed: %s", proc.stderr)
            return None

        size_kb = os.path.getsize(output_path) / 1024
        logger.info("TTS final audio: %s (%.1f KB)", output_path, size_kb)
        return output_path

    finally:
        # Clean up temp files
        for p in chunk_paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.remove(filelist_path)
        except (OSError, UnboundLocalError):
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
