from __future__ import annotations

"""
TTS Client — Wrapper cho Núi Trúc TTS API.

API: http://tts.nuitruc.ai/api/tts
- POST JSON: {"text": "...", "voice_id": "voice1", "speed": 1.0}
- Output: WAV (Content-Type: audio/wav)
- Text is split into ~700-char chunks at sentence boundaries to avoid
  HTTP 504 timeouts on long scripts. Chunks are concatenated via ffmpeg.
"""

import json
import logging
import os
import re
import shutil
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

CHUNK_MAX_CHARS = 700           # Max chars per TTS request — keep under API limit
TTS_TIMEOUT = 60                # Seconds per chunk request (short text → fast response)
TTS_MAX_RETRIES = 3             # Retry count for transient errors (503, 500, timeout)
TTS_RETRY_DELAY = 5             # Initial backoff seconds (doubles each retry)


def _split_into_chunks(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split text into chunks at sentence boundaries, at most max_chars each.

    Splits on '.', '!', '?' boundaries (keeping the punctuation with its
    sentence). If a single sentence exceeds max_chars, it is hard-split at
    the last whitespace before the limit.
    """
    # Split into sentences, keeping trailing punctuation attached
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        candidate = (current + " " + sentence).strip() if current else sentence

        if len(candidate) <= max_chars:
            current = candidate
        else:
            # Flush current chunk before starting a new one
            if current:
                chunks.append(current)
            current = sentence

            # Handle sentences that are themselves too long
            while len(current) > max_chars:
                split_at = current.rfind(" ", 0, max_chars)
                if split_at == -1:
                    split_at = max_chars
                chunks.append(current[:split_at].strip())
                current = current[split_at:].strip()

    if current:
        chunks.append(current)

    return chunks


def _concat_audio_files(chunk_paths: list[str], output_path: str) -> str | None:
    """Concatenate WAV files into one output file using ffmpeg concat demuxer."""
    if len(chunk_paths) == 1:
        shutil.move(chunk_paths[0], output_path)
        return output_path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for path in chunk_paths:
            # ffmpeg concat list requires escaped paths
            f.write(f"file '{path}'\n")
        list_path = f.name

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", output_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.error("ffmpeg concat failed: %s", result.stderr)
            return None
        size_kb = os.path.getsize(output_path) / 1024
        logger.info("TTS concat → %s (%.1f KB)", output_path, size_kb)
        return output_path
    except Exception as e:
        logger.error("ffmpeg concat error: %s", e)
        return None
    finally:
        os.unlink(list_path)


def text_to_speech(text: str, output_path: str) -> str | None:
    """Convert text to speech audio file.

    Splits text into chunks of at most CHUNK_MAX_CHARS characters at sentence
    boundaries, calls the TTS API for each chunk, then concatenates the
    resulting WAV files via ffmpeg.

    Args:
        text: Script text to convert.
        output_path: Path to save final audio file (.wav).

    Returns:
        Path to the audio file, or None on failure.
    """
    if not config.TTS_API_URL:
        logger.error("TTS_API_URL not configured")
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    chunks = _split_into_chunks(text)
    logger.info("TTS: %d chars → %d chunk(s)", len(text), len(chunks))

    if len(chunks) == 1:
        return _tts_single(chunks[0], output_path)

    # Multi-chunk path: write each chunk to a temp WAV, then concatenate
    tmp_dir = tempfile.mkdtemp(prefix="tts_chunks_")
    chunk_paths: list[str] = []
    try:
        for i, chunk in enumerate(chunks):
            chunk_path = os.path.join(tmp_dir, f"chunk_{i:03d}.wav")
            logger.info("TTS chunk %d/%d (%d chars)", i + 1, len(chunks), len(chunk))
            result = _tts_single(chunk, chunk_path)
            if result is None:
                logger.error("TTS failed on chunk %d/%d — aborting", i + 1, len(chunks))
                return None
            chunk_paths.append(chunk_path)

        return _concat_audio_files(chunk_paths, output_path)
    finally:
        for p in chunk_paths:
            if os.path.exists(p):
                try:
                    os.unlink(p)
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
    print(f"Chunk max chars: {CHUNK_MAX_CHARS}")
    # Test ffprobe
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=5)
        print("ffprobe: OK")
    except FileNotFoundError:
        print("ffprobe: NOT FOUND — brew install ffmpeg")

    # Smoke-test the chunker
    sample = (
        "Xin chào! Đây là đoạn văn bản dài để kiểm tra. "
        "Câu đầu tiên kết thúc ở đây. Câu thứ hai tiếp theo. "
        "Câu thứ ba. Câu thứ tư. Câu thứ năm với nội dung dài hơn một chút để kiểm tra. "
        "Đây là câu cuối cùng trong đoạn văn bản thử nghiệm này!"
    )
    chunks = _split_into_chunks(sample, max_chars=100)
    print(f"\nChunker test ({len(sample)} chars → {len(chunks)} chunks):")
    for i, c in enumerate(chunks):
        print(f"  [{i+1}] ({len(c)} chars) {c!r}")
