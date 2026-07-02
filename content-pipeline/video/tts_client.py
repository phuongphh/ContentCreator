from __future__ import annotations

"""
TTS Client — Wrapper cho Núi Trúc TTS API (async job flow).

Base URL: config.TTS_API_URL (default http://tts.nuitruc.ai/api/tts)

Long scripts no longer fit the old synchronous POST /api/tts (it timed out), so
the client uses the async job API derived from the same base URL:

  1. POST {base}/submit        {"text", "voice_id", "speed"} -> {"job_id": ...}
  2. GET  {base}/status/<id>   poll every TTS_POLL_INTERVAL s until "done"/"error"
  3. GET  {base}/result/<id>   download the WAV (one-shot; 404 on a 2nd call)

All steps are bounded (TTS_REQUEST_TIMEOUT per request, TTS_POLL_TIMEOUT overall,
TTS_POLL_MAX_FAILURES consecutive poll errors) so a stalled/never-finishing job
fails fast and the provider fallback chain (video.tts.factory) takes over
instead of stalling the cron window (issue #58).
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
TTS_TIMEOUT = config.TTS_TIMEOUT        # result-download socket timeout (s)
TTS_MAX_RETRIES = config.TTS_MAX_RETRIES  # retries for fast transient HTTP errors
TTS_RETRY_DELAY = config.TTS_RETRY_DELAY  # initial backoff (s), exponential
# Async job-flow knobs (mirrored as module globals so tests can patch them).
TTS_REQUEST_TIMEOUT = config.TTS_REQUEST_TIMEOUT  # submit/status socket timeout (s)
TTS_POLL_INTERVAL = config.TTS_POLL_INTERVAL      # seconds between status polls
TTS_POLL_TIMEOUT = config.TTS_POLL_TIMEOUT        # max total wait for a job (s)
TTS_POLL_MAX_FAILURES = config.TTS_POLL_MAX_FAILURES  # consecutive poll errors before failover


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


def _endpoint(path: str) -> str:
    """Build an async-API sub-endpoint URL from the configured TTS base URL.

    ``config.TTS_API_URL`` is the base (default http://tts.nuitruc.ai/api/tts);
    the job API exposes ``/submit``, ``/status/<id>`` and ``/result/<id>`` under
    it. A trailing slash on the base is tolerated.
    """
    base = (config.TTS_API_URL or "").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _headers() -> dict:
    """Common request headers (adds bearer auth when TTS_API_KEY is set)."""
    headers = {"Content-Type": "application/json"}
    if config.TTS_API_KEY:
        headers["Authorization"] = f"Bearer {config.TTS_API_KEY}"
    return headers


def _open_with_retry(opener, url: str, *, data: bytes | None, timeout: int,
                     what: str) -> bytes | None:
    """Run one HTTP request through the shared retry / fail-fast loop.

    Retries only fast transient HTTP errors (429/5xx) with exponential backoff;
    timeouts, SSL and other errors fail fast so the provider fallback chain can
    take over (issue #58). Returns the raw response body on success, else None.
    Errors are logged without leaking the Authorization header.
    """
    last_exc: Exception | None = None
    for attempt in range(1, TTS_MAX_RETRIES + 1):
        try:
            req = Request(url, data=data, headers=_headers())
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except (ssl.SSLError, URLError, HTTPError, OSError) as e:
            last_exc = e
            if _is_retryable(e) and attempt < TTS_MAX_RETRIES:
                wait = TTS_RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning("TTS %s attempt %d/%d failed (%s), retrying in %ds...",
                               what, attempt, TTS_MAX_RETRIES, e, wait)
                time.sleep(wait)
                continue
            break
        except Exception as e:
            last_exc = e
            break

    if _is_timeout(last_exc):
        logger.error("TTS %s timed out after %ds — failing over to next provider",
                     what, timeout)
    else:
        logger.error("TTS %s failed: %s", what, last_exc)
    return None


def _submit_job(opener, text: str) -> str | None:
    """POST /submit and return the job id, or None on failure."""
    payload = json.dumps({
        "text": text,
        "voice_id": config.TTS_VOICE_ID or "preset_my_duyen",
        "speed": config.TTS_VOICE_SPEED,
    }).encode("utf-8")
    body = _open_with_retry(opener, _endpoint("submit"), data=payload,
                            timeout=TTS_REQUEST_TIMEOUT, what="submit")
    if body is None:
        return None
    try:
        data = json.loads(body)
        job_id = data.get("job_id") or data.get("id")
    except (ValueError, AttributeError):
        job_id = None
    if not job_id:
        logger.error("TTS submit returned no job_id (body: %.200r)", body)
        return None
    logger.info("TTS job submitted: %s", job_id)
    return str(job_id)


def _await_job(opener, job_id: str) -> bool:
    """Poll /status until the job is done. Return True on success, else False.

    Bounded by TTS_POLL_TIMEOUT overall and TTS_POLL_MAX_FAILURES consecutive
    poll errors, so a stalled status endpoint fails over fast (issue #58).
    """
    deadline = time.monotonic() + TTS_POLL_TIMEOUT
    consecutive_failures = 0
    while True:
        body = _open_with_retry(opener, _endpoint(f"status/{job_id}"), data=None,
                                timeout=TTS_REQUEST_TIMEOUT, what="status")
        if body is None:
            consecutive_failures += 1
            if consecutive_failures >= TTS_POLL_MAX_FAILURES:
                logger.error("TTS status polling failed %d× for job %s — failing over",
                             consecutive_failures, job_id)
                return False
        else:
            consecutive_failures = 0
            try:
                status = json.loads(body).get("status")
            except (ValueError, AttributeError):
                status = None
            if status == "done":
                return True
            if status == "error":
                logger.error("TTS job %s reported status=error — failing over", job_id)
                return False
            logger.debug("TTS job %s status=%s — still polling", job_id, status)

        if time.monotonic() >= deadline:
            logger.error("TTS job %s not done within %ds — failing over to next provider",
                         job_id, TTS_POLL_TIMEOUT)
            return False
        time.sleep(TTS_POLL_INTERVAL)


def _tts_single(text: str, output_path: str) -> str | None:
    """Synthesize one text chunk via the Núi Trúc async job API.

    submit -> poll /status -> download /result (one-shot). Returns the output
    path on success, else None so the factory can fall back to the next provider.
    The /result download is fetched into memory then written, and is retried only
    on transient 5xx (which means it was NOT delivered, so the one-shot job is not
    yet consumed) — never after a successful 200.
    """
    # Secure-by-default opener (verifies TLS unless TTS_ALLOW_INSECURE_SSL).
    opener = _build_opener()

    job_id = _submit_job(opener, text)
    if not job_id:
        return None

    if not _await_job(opener, job_id):
        return None

    body = _open_with_retry(opener, _endpoint(f"result/{job_id}"), data=None,
                            timeout=TTS_TIMEOUT, what="result")
    if body is None:
        logger.error("TTS result download failed for job %s", job_id)
        return None

    try:
        with open(output_path, "wb") as f:
            f.write(body)
    except OSError as e:
        logger.error("Failed to write TTS audio to %s: %s", output_path, e)
        return None

    size_kb = os.path.getsize(output_path) / 1024
    logger.info("TTS chunk saved: %s (%.1f KB) [job %s]", output_path, size_kb, job_id)
    return output_path


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
