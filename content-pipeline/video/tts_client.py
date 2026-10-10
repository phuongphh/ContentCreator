from __future__ import annotations

"""
TTS Client — Wrapper cho Núi Trúc TTS API (v1 async job flow, v2 đồng bộ).

Hai version sống song song, chọn bằng ``config.TTS_API_VERSION``:

**v1 (mặc định)** — job API bất đồng bộ trên ``config.TTS_API_URL``
(https://tts.nuitruc.ai/api/tts), 3 bước để không giữ kết nối lâu:

  1. POST {base}/submit        {"text", "voice_id", "speed"} -> {"job_id": ...}
  2. GET  {base}/status/<id>   poll every TTS_POLL_INTERVAL s (10–15s) until
                               "done"/"error"
  3. GET  {base}/result/<id>   download the WAV (one-shot; 404 on a 2nd call —
                               job đã bị xoá, đó là hành vi bình thường)

**v2 (tuỳ chọn, TTS_API_VERSION=v2)** — endpoint kiểu OpenAI, ĐỒNG BỘ, một
request duy nhất:

    POST {config.TTS_V2_API_URL}          (https://tts2.nuitruc.ai/v1/audio/speech)
    Authorization: Bearer {config.TTS_API_KEY}     # BẮT BUỘC ở v2
    {"model", "input", "voice", "cfg_value", "inference_timesteps", "speed"}
    -> body CHÍNH LÀ bytes audio (không còn job_id / poll / download)

Cả hai đường đều đi qua cùng một tầng HTTP (secure-by-default SSL, phân loại
lỗi, retry chỉ cho 429/5xx). Mọi bước có trần thời gian (TTS_V2_TIMEOUT cho v2;
TTS_REQUEST_TIMEOUT / TTS_POLL_TIMEOUT / TTS_POLL_MAX_FAILURES cho v1) nên
endpoint treo vẫn fail nhanh để chain fallback (video.tts.factory) tiếp quản
thay vì ăn hết cửa sổ cron (issue #58).
"""

import json
import logging
import os
import re
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
# v2 sinh audio trong đúng MỘT request đồng bộ nên cần socket timeout rộng hơn
# hẳn timeout tải file của v1.
TTS_V2_TIMEOUT = config.TTS_V2_TIMEOUT


def text_to_speech(text: str, output_path: str, voice_id: str | None = None,
                   speed: float | None = None) -> str | None:
    """Convert text to speech audio file (facade over the TTS provider factory).

    Dispatches to the provider chosen by ``config.TTS_PROVIDER`` and falls back
    to the other providers on failure (P2). Text is expected to be already
    speech-normalized by the caller (preprocess_for_tts).

    Args:
        text: Script text to convert.
        output_path: Path to save final audio file.
        voice_id: Optional per-provider voice override (Phase 4). None uses
            the provider's own config-driven default.
        speed: Optional 1.0-relative playback rate. None uses
            config.TTS_VOICE_SPEED.

    Returns:
        Path to the audio file, or None on failure.
    """
    from video.tts.factory import synthesize
    return synthesize(text, output_path, voice_id=voice_id, speed=speed)


def synthesize_for_track(text: str, track: str, output_path: str) -> str | None:
    """Synthesize using the voice + speed configured for `track` ('ai' | 'drama').

    Voice/speed resolve from config.tts_profile_for_track (single source of
    truth): ai → voice1, drama → preset_my_duyen; tốc độ mặc định theo version
    API (v2: 0.8 cả hai track — hai engine đọc khác nhau; v1: 1.5 / 1.0). Tất
    cả env-overridable. An empty voice id → provider default voice (silently
    reusing a default voice is far safer than producing no audio).
    """
    voice_id, speed = config.tts_profile_for_track(track)
    return text_to_speech(text, output_path, voice_id=voice_id, speed=speed)


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

    ``config.TTS_API_URL`` is the base (default https://tts.nuitruc.ai/api/tts);
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


def _redact(text: str) -> str:
    """Che token khỏi một chuỗi trước khi ghi log.

    Cùng tinh thần `telegram_bot._redact` (issue #119): log pipeline hay được
    dán vào issue khi debug. Endpoint TTS là **cấu hình được**, và có gateway
    dội lại request headers trong body lỗi — nên không thể coi "body do server
    trả về thì chắc chắn không chứa token của mình" là điều hiển nhiên. Che cả
    giá trị token đang cấu hình lẫn mọi cụm "Bearer <gì đó>".
    """
    token = getattr(config, "TTS_API_KEY", "") or ""
    if token:
        text = text.replace(token, "***")
    return re.sub(r"(?i)bearer\s+\S+", "Bearer ***", text)


def _error_detail(exc: Exception | None) -> str:
    """Short, safe description of a failed request (adds the server's own body).

    ``str(HTTPError)`` is only "HTTP Error 400: Bad Request" — useless when the
    endpoint rejects e.g. an unknown voice id, because the *reason* lives in the
    response body. The body is truncated (a huge HTML error page must not flood
    the log) và đi qua `_redact` trước khi ra log.
    """
    if not isinstance(exc, HTTPError):
        return _redact(str(exc))
    try:
        body = exc.read().decode("utf-8", "replace").strip()
    except Exception:  # body already consumed / not readable
        body = ""
    detail = f"{exc} — {body[:300]}" if body else str(exc)
    return _redact(detail)


def _open_with_retry(opener, url: str, *, data: bytes | None, timeout: int,
                     what: str, headers_out: dict | None = None) -> bytes | None:
    """Run one HTTP request through the shared retry / fail-fast loop.

    Retries only fast transient HTTP errors (429/5xx) with exponential backoff;
    timeouts, SSL and other errors fail fast so the provider fallback chain can
    take over (issue #58). Returns the raw response body on success, else None.
    Errors are logged without leaking the Authorization header.

    ``headers_out``: optional dict filled with the RESPONSE headers on success
    (v2 needs Content-Type to tell audio bytes from a JSON error served with
    HTTP 200).
    """
    last_exc: Exception | None = None
    for attempt in range(1, TTS_MAX_RETRIES + 1):
        try:
            req = Request(url, data=data, headers=_headers())
            with opener.open(req, timeout=timeout) as resp:
                if headers_out is not None:
                    try:
                        headers_out.update(dict(resp.headers.items()))
                    except Exception:  # pragma: no cover - exotic response objects
                        pass
                return resp.read()
        except (ssl.SSLError, URLError, HTTPError, OSError) as e:
            last_exc = e
            if _is_retryable(e) and attempt < TTS_MAX_RETRIES:
                wait = TTS_RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning("TTS %s attempt %d/%d failed (%s), retrying in %ds...",
                               what, attempt, TTS_MAX_RETRIES,
                               _error_detail(e), wait)
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
        logger.error("TTS %s failed: %s", what, _error_detail(last_exc))
    return None


def _use_v2() -> bool:
    """True khi dùng endpoint TTS v2 đồng bộ (tuỳ chọn, mặc định là v1).

    CHỈ giá trị "v2" rõ ràng mới bật v2; giá trị lạ (gõ sai) rơi về v1 = job API
    đang là mặc định, vì v2 BẮT BUỘC có bearer token — đoán v2 khi gõ nhầm nghĩa
    là chuyển sang một đường cần credential mà người dùng chưa chắc đã cấu hình.
    config.validate_flags() cảnh báo giá trị lạ.
    """
    return (getattr(config, "TTS_API_VERSION", "v1") or "v1").strip().lower() == "v2"


def _v2_endpoint() -> str:
    """Full URL của endpoint speech v2 (không có sub-path như v1)."""
    return (getattr(config, "TTS_V2_API_URL", "") or "").strip()


def _v2_payload(text: str, voice_id: str | None = None,
                speed: float | None = None) -> bytes:
    """JSON body cho POST /v1/audio/speech — đúng bộ field API v2 nhận.

    Field đổi tên so với v1: ``input`` (không phải ``text``) và ``voice``
    (không phải ``voice_id``). ``cfg_value``/``inference_timesteps`` là tham số
    riêng của engine v2 (config, env-overridable); ``speed`` giữ nguyên ý nghĩa
    hệ số 1.0-relative nên per-track speed vẫn chảy xuống đây như cũ.
    """
    return json.dumps({
        "model": config.TTS_V2_MODEL,
        "input": text,
        "voice": voice_id or config.TTS_VOICE_ID or "voice1",
        "cfg_value": config.TTS_V2_CFG_VALUE,
        "inference_timesteps": config.TTS_V2_INFERENCE_TIMESTEPS,
        "speed": config.TTS_VOICE_SPEED if speed is None else speed,
    }, ensure_ascii=False).encode("utf-8")


def _looks_like_json_error(body: bytes, content_type: str) -> bool:
    """True nếu response là JSON/text lỗi chứ không phải bytes audio.

    Một số gateway trả lỗi kèm HTTP 200 (hoặc quên set Content-Type): ghi thẳng
    body đó ra file .mp3 sẽ tạo một "audio" hỏng mà ffmpeg chỉ báo lỗi mãi sau,
    ở bước dựng video. Bắt ngay tại đây rẻ hơn nhiều.
    """
    ctype = (content_type or "").lower()
    if "json" in ctype or ctype.startswith("text/"):
        return True
    # Body JSON hợp lệ vẫn có thể mở đầu bằng BOM hoặc xuống dòng/khoảng trắng —
    # soi đúng byte đầu tiên sẽ xếp nhầm nó là audio, ghi rác ra .mp3 và "thành
    # công" (chặn mất fallback) tới tận lúc ffmpeg dựng video mới lộ.
    head = body[:64].lstrip(b"\xef\xbb\xbf").lstrip()
    return head[:1] in (b"{", b"[")


def _tts_v2_single(text: str, output_path: str, voice_id: str | None = None,
                   speed: float | None = None) -> str | None:
    """Synthesize một đoạn text qua endpoint v2 ĐỒNG BỘ (một request duy nhất).

    Trả về output_path khi thành công, None khi lỗi (để factory fallback sang
    provider kế tiếp). Khác v1: không có job id, không poll — body của response
    CHÍNH LÀ audio.
    """
    url = _v2_endpoint()
    if not url:
        logger.error("TTS_V2_API_URL not configured — nuitruc v2 unavailable")
        return None
    if not config.TTS_API_KEY:
        # v2 bắt buộc Bearer token. Gửi đi không token chỉ để ăn 401 rồi rơi
        # sang giọng máy edge — nói thẳng nguyên nhân ngay tại đây.
        logger.error("TTS_API_KEY is empty — Núi Trúc TTS v2 requires a bearer "
                     "token (nt_sec_...). Set TTS_API_KEY in .env")
        return None

    opener = _build_opener()
    resp_headers: dict = {}
    body = _open_with_retry(opener, url,
                            data=_v2_payload(text, voice_id=voice_id, speed=speed),
                            timeout=TTS_V2_TIMEOUT, what="speech (v2)",
                            headers_out=resp_headers)
    if body is None:
        return None
    if not body:
        logger.error("TTS v2 returned an empty body — failing over to next provider")
        return None
    # Tên header không phân biệt hoa/thường trên đường truyền — chuẩn hoá key
    # trước khi tra, đừng phụ thuộc server viết đúng "Content-Type".
    ctype = next((v for k, v in resp_headers.items()
                  if k.lower() == "content-type"), "")
    if _looks_like_json_error(body, ctype):
        # Thường gặp: voice id không tồn tại trên v2, hoặc model sai tên.
        logger.error("TTS v2 returned a non-audio response (%s) — check "
                     "TTS_V2_MODEL / voice id",
                     _redact(repr(body[:200])))
        return None

    try:
        with open(output_path, "wb") as f:
            f.write(body)
    except OSError as e:
        logger.error("Failed to write TTS audio to %s: %s", output_path, e)
        return None

    size_kb = os.path.getsize(output_path) / 1024
    logger.info("TTS v2 audio saved: %s (%.1f KB)", output_path, size_kb)
    return output_path


def _submit_job(opener, text: str, voice_id: str | None = None,
                speed: float | None = None) -> str | None:
    """POST /submit and return the job id, or None on failure."""
    payload = json.dumps({
        "text": text,
        "voice_id": voice_id or config.TTS_VOICE_ID or "preset_my_duyen",
        "speed": config.TTS_VOICE_SPEED if speed is None else speed,
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


def _tts_single(text: str, output_path: str, voice_id: str | None = None,
                speed: float | None = None) -> str | None:
    """Synthesize one text chunk via Núi Trúc TTS (v1 job API | v2 đồng bộ).

    v1: submit -> poll /status -> download /result (one-shot). Returns the output
    path on success, else None so the factory can fall back to the next provider.
    The /result download is fetched into memory then written, and is retried only
    on transient 5xx (which means it was NOT delivered, so the one-shot job is not
    yet consumed) — never after a successful 200.

    ``voice_id`` (Phase 4) overrides ``config.TTS_VOICE_ID`` and ``speed``
    (per-track) overrides ``config.TTS_VOICE_SPEED`` for this call only.

    Khi ``config.TTS_API_VERSION`` là v2 (tuỳ chọn) thì cả flow này được thay
    bằng một request đồng bộ tới endpoint v2 (_tts_v2_single).
    """
    if _use_v2():
        return _tts_v2_single(text, output_path, voice_id=voice_id, speed=speed)

    # Secure-by-default opener (verifies TLS unless TTS_ALLOW_INSECURE_SSL).
    opener = _build_opener()

    job_id = _submit_job(opener, text, voice_id=voice_id, speed=speed)
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
    # Smoke test thủ công (KHÔNG chạy trong pipeline):
    #   python -m video.tts_client                      # in cấu hình đang dùng
    #   python -m video.tts_client --say "Xin chào" [--track drama] [--out a.mp3]
    # Dùng --say để kiểm tra nhanh sau khi đổi version/voice/token: nó gọi
    # ĐÚNG đường mà pipeline gọi, nên lỗi voice id hay token sai lộ ra ngay
    # thay vì tới lúc render video mới biết.
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Núi Trúc TTS smoke test")
    parser.add_argument("--say", help="Text to synthesize (bỏ trống = chỉ in cấu hình)")
    parser.add_argument("--track", default="ai", choices=["ai", "drama"],
                        help="Voice/speed profile để thử (mặc định: ai)")
    parser.add_argument("--out", default="tts_selftest.mp3", help="File audio output")
    args = parser.parse_args()

    version = "v2" if _use_v2() else "v1"
    endpoint = _v2_endpoint() if _use_v2() else config.TTS_API_URL
    voice, speed = config.tts_profile_for_track(args.track)
    print(f"TTS version:  {version} (TTS_API_VERSION={config.TTS_API_VERSION})")
    print(f"TTS endpoint: {endpoint or '(not set)'}")
    if config.TTS_API_KEY:
        key_note = "set"
    elif version == "v2":
        key_note = "MISSING (v2 bắt buộc)"
    else:
        key_note = "not set (v1: tuỳ chọn)"
    print(f"TTS API key:  {key_note}")
    if version == "v2":
        print(f"TTS model:    {config.TTS_V2_MODEL} "
              f"(cfg_value={config.TTS_V2_CFG_VALUE}, "
              f"inference_timesteps={config.TTS_V2_INFERENCE_TIMESTEPS})")
    print(f"Track {args.track}: voice={voice or '(provider default)'} speed={speed}")
    # Test ffprobe
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=5)
        print("ffprobe: OK")
    except FileNotFoundError:
        print("ffprobe: NOT FOUND — brew install ffmpeg")

    if args.say:
        # Gọi thẳng provider Núi Trúc (không qua factory) để lỗi KHÔNG bị che
        # bởi fallback sang edge — smoke test phải nói thật là v2 sống hay chết.
        path = _tts_single(args.say, args.out, voice_id=voice, speed=speed)
        if path:
            print(f"OK: {path} ({get_audio_duration(path):.1f}s)")
        else:
            print("FAILED — xem log phía trên (token? voice id? endpoint?)")
            sys.exit(1)
