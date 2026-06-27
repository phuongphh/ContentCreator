from __future__ import annotations

"""
TTS Client — Wrapper cho Núi Trúc TTS API.

API: http://tts.nuitruc.ai/api/tts
- POST JSON: {"text": "...", "voice_id": "voice1", "speed": 1.0}
- Output: WAV (Content-Type: audio/wav)
- Timeout: config.TTS_TIMEOUT (default 120s) — fail fast on a stalled endpoint so
  the provider fallback chain (video.tts.factory) can take over (issue #58).
"""

import json
import logging
import os
import ssl
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

# HTTP tuning lives in config (env-overridable). Bound here as module globals so
# tests can patch them and the retry loop reads a single source of truth.
TTS_TIMEOUT = config.TTS_TIMEOUT        # per-request socket timeout (s)
TTS_MAX_RETRIES = config.TTS_MAX_RETRIES  # retries for fast transient HTTP errors
TTS_RETRY_DELAY = config.TTS_RETRY_DELAY  # initial backoff (s), exponential


def text_to_speech(text: str, output_path: str) -> str | None:
    """Convert text to speech audio file (facade over the TTS provider factory).

    Dispatches to the provider chosen by ``config.TTS_PROVIDER`` and falls back
    to the other providers on failure (P2). Text is expected to be already
    speech-normalized by the caller (preprocess_for_tts).

    Args:
        text: Script text to convert.
        output_path: Path to save final audio file.

    Returns:
        Path to the audio file, or None on failure.
    """
    from video.tts.factory import synthesize
    return synthesize(text, output_path)


def _build_opener(insecure: bool | None = None) -> object:
    """Build a urllib opener for the TTS endpoint.

    Secure by default: verifies the server certificate against the system CA
    store (and any HTTP→HTTPS redirect inherits the same context, which urllib
    does not do for the global default context).

    TLS verification is disabled ONLY when explicitly opted in via
    ``config.TTS_ALLOW_INSECURE_SSL`` (env ``TTS_ALLOW_INSECURE_SSL=1``). This
    exists for a known self-signed endpoint; it is a MITM risk, so it logs a
    warning whenever active.

    Args:
        insecure: Override the config flag (used in tests). When None, reads
            ``config.TTS_ALLOW_INSECURE_SSL``.
    """
    if insecure is None:
        insecure = getattr(config, "TTS_ALLOW_INSECURE_SSL", False)

    ssl_ctx = ssl.create_default_context()  # verify ON, check_hostname ON
    if insecure:
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        logger.warning(
            "TTS SSL verification DISABLED (TTS_ALLOW_INSECURE_SSL=1) — "
            "connection is vulnerable to MITM. Use only for a trusted endpoint."
        )
    return build_opener(HTTPSHandler(context=ssl_ctx))


def _is_retryable(exc: Exception) -> bool:
    """Return True only for *fast* transient errors worth retrying on the same URL.

    Retrying a timeout (or SSL error) is deliberately excluded: the reported
    failure mode (issue #58) is a stalled endpoint — TCP connects but no response
    — so a retry would just burn another full TTS_TIMEOUT and blow the cron
    window. Resilience against a dead endpoint comes from the provider fallback
    chain (video.tts.factory), not from re-hitting the same dead URL. HTTP
    429/5xx, by contrast, return quickly, so retrying them with backoff is cheap
    and often succeeds.
    """
    return isinstance(exc, HTTPError) and exc.code in (429, 500, 502, 503, 504)


def _is_timeout(exc: Exception | None) -> bool:
    """Return True if *exc* is (or wraps) a socket timeout.

    ``socket.timeout`` is an alias of ``TimeoutError`` since Python 3.10, and
    urllib surfaces read/connect timeouts as ``URLError`` wrapping it.
    """
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, URLError):
        return isinstance(getattr(exc, "reason", None), TimeoutError)
    return False


def _tts_single(text: str, output_path: str) -> str | None:
    """Call the TTS API for one text chunk.

    Retries only fast transient HTTP errors (429/5xx) with exponential backoff;
    timeouts and SSL errors fail fast so the provider fallback chain can take
    over (issue #58). Returns the output path on success, else None.
    """
    payload = json.dumps({
        "text": text,
        "voice_id": config.TTS_VOICE_ID or "voice1",
        "speed": config.TTS_VOICE_SPEED,
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if config.TTS_API_KEY:
        headers["Authorization"] = f"Bearer {config.TTS_API_KEY}"

    url = config.TTS_API_URL

    # Build opener with a verifying SSL context (secure by default) that also
    # applies to HTTP→HTTPS redirects. Verification is only disabled when
    # TTS_ALLOW_INSECURE_SSL is explicitly set.
    opener = _build_opener()

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
            if _is_retryable(e) and attempt < TTS_MAX_RETRIES:
                wait = TTS_RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning("TTS attempt %d/%d failed (%s), retrying in %ds...",
                               attempt, TTS_MAX_RETRIES, e, wait)
                time.sleep(wait)
                continue
            # Timeout / SSL / non-retryable error — fail fast so the factory can
            # fall back to the next provider instead of stalling the cron window.
            break

        except Exception as e:
            last_exc = e
            break

    if _is_timeout(last_exc):
        logger.error("TTS endpoint timed out after %ds (%s) — failing over to "
                     "next provider", TTS_TIMEOUT, config.TTS_API_URL)
    else:
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
