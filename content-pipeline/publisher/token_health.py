from __future__ import annotations

"""
OAuth token health check — issue #94.

Giám sát OAuth token của MỌI kênh YouTube khai báo trong channels.py và alert
Telegram khi token chết. Sinh ra để bịt 3 root cause của issue #94:

1. Token drama_youtube hết hạn + refresh_token bị thu hồi (invalid_grant) từ
   10/07 mà không ai biết → video 117 kẹt, không upload được.
2. Cron check cũ chỉ soi MỘT file token cứng (publisher/.youtube_token.json =
   token mặc định của ai_youtube), bỏ sót drama_youtube và mọi kênh thêm sau.
   Ở đây ta lặp qua channels.channels_for_platform("youtube") — đúng nguyên tắc
   channels.py là single source of truth (như phần còn lại của Phase 1/5).
3. Cron check cũ timeout 30s rồi bị kill 13 lần liên tiếp. Nguyên nhân:
   `creds.refresh(Request())` của google-auth KHÔNG đặt socket timeout, nên nếu
   token endpoint chậm/treo thì request treo vô hạn. Ở đây ta probe refresh_token
   trực tiếp bằng stdlib urllib với socket timeout có giới hạn (fail-fast) —
   giống cách reddit_client.py chỉ dùng stdlib + phân biệt lỗi cứng/tạm thời.

Thiết kế:
- Chỉ **probe** (thử mint access token mới từ refresh_token), KHÔNG ghi đè file
  token — không rotate token của uploader → không đua ghi/không đổi trạng thái
  đang chạy tốt.
- Phân biệt lỗi ĐỊNH DANH (invalid_grant = thu hồi/hết hạn, thiếu file, thiếu
  refresh_token, sai client) với lỗi TẠM THỜI (timeout/mạng/5xx/429) để không
  spam Telegram khi Google chỉ chập chờn. Lỗi tạm thời chỉ alert khi lặp lại
  TOKEN_HEALTH_TRANSIENT_ALERT_AFTER lần liên tiếp — để chính "monitor không
  tới được Google" cũng lộ ra (đúng bài học root cause #3), đếm qua
  storage.pipeline_state (bền vững giữa các lần chạy).
- KHÔNG log giá trị token/secret/refresh_token — chỉ log tên kênh + trạng thái +
  thông báo lỗi text của Google (an toàn).

Chạy độc lập:  python -m publisher.token_health
launchd:       launchd/com.ai5phut.token-health.plist (08:00 hằng ngày)
Defense-in-depth: main.run_pipeline gọi best-effort như launchd_status.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
import channels
from publisher.youtube_uploader import resolve_token_file

logger = logging.getLogger(__name__)

# Endpoint refresh mặc định nếu file token thiếu "token_uri" (google creds
# luôn ghi field này, nhưng token cũ/thủ công có thể thiếu).
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Mã trạng thái. `alert=True` = cần báo động ngay; "transient" đi qua bộ đếm.
OK = "ok"
REVOKED = "revoked"                 # invalid_grant — token thu hồi/hết hạn (#94)
MISSING = "missing"                 # không tìm thấy file token
UNREADABLE = "unreadable"           # file tồn tại nhưng JSON hỏng/thiếu field
NO_REFRESH_TOKEN = "no_refresh_token"
MISCONFIG = "misconfig"             # invalid_client / 4xx khác — sai cấu hình
TRANSIENT = "transient"             # timeout/mạng/5xx/429 — thử lại lần sau

# Trạng thái báo động ngay (định danh, endpoint đã trả lời hoặc lỗi cục bộ).
_ALERT_NOW = {REVOKED, MISSING, UNREADABLE, NO_REFRESH_TOKEN, MISCONFIG}


class TokenCheckResult:
    """Kết quả kiểm tra 1 kênh."""

    __slots__ = ("channel_key", "channel_name", "token_file", "code", "detail")

    def __init__(self, channel_key, channel_name, token_file, code, detail=""):
        self.channel_key = channel_key
        self.channel_name = channel_name
        self.token_file = token_file
        self.code = code
        self.detail = detail

    @property
    def healthy(self) -> bool:
        return self.code == OK

    def __repr__(self) -> str:
        return (f"TokenCheckResult({self.channel_key}, {self.code}"
                + (f", {self.detail!r}" if self.detail else "") + ")")


def _read_token_file(path: str):
    """Đọc file token JSON.

    Returns (data, code): data là dict khi đọc được; code là None khi OK, hoặc
    MISSING / UNREADABLE khi lỗi (data=None).
    """
    if not path or not os.path.exists(path):
        return None, MISSING
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("Token file %s không đọc được: %s", path, e)
        return None, UNREADABLE
    if not isinstance(data, dict):
        return None, UNREADABLE
    return data, None


def _probe_refresh(token: dict, timeout: int):
    """Thử mint access token mới từ refresh_token (KHÔNG ghi lại file).

    Trả (code, detail). Chỉ dùng stdlib urllib với socket timeout — HTTPS verify
    mặc định (KHÔNG tắt SSL). Không bao giờ đưa refresh_token/secret vào detail.
    """
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return NO_REFRESH_TOKEN, "file token không có refresh_token"

    client_id = token.get("client_id")
    client_secret = token.get("client_secret")
    if not client_id or not client_secret:
        return MISCONFIG, "file token thiếu client_id/client_secret"

    token_uri = token.get("token_uri") or _DEFAULT_TOKEN_URI
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")

    req = urllib.request.Request(
        token_uri, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(1)  # 200 = refresh_token còn sống; không cần giữ token mới
        return OK, ""
    except urllib.error.HTTPError as e:
        return _classify_http_error(e)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Timeout / DNS / reset / SSL — tạm thời, thử lại lần sau (fail-fast nhờ
        # socket timeout ở trên thay vì treo như creds.refresh() của google-auth).
        return TRANSIENT, f"không kết nối được endpoint OAuth: {e}"


def _classify_http_error(e: "urllib.error.HTTPError"):
    """Phân loại HTTPError từ token endpoint → (code, detail)."""
    status = e.code
    error = ""
    description = ""
    try:
        payload = json.loads(e.read().decode("utf-8", "replace"))
        if isinstance(payload, dict):
            error = str(payload.get("error", "") or "")
            description = str(payload.get("error_description", "") or "")
    except (ValueError, OSError):
        pass

    detail = (f"{error}: {description}".strip(": ")) or f"HTTP {status}"

    if error == "invalid_grant":
        # Chính xác lỗi issue #94: refresh_token bị thu hồi hoặc hết hạn.
        return REVOKED, detail
    if status in (401,) or error in ("invalid_client", "unauthorized_client"):
        return MISCONFIG, detail
    if status == 429 or status >= 500:
        return TRANSIENT, detail
    # 400 khác invalid_grant (invalid_request...) = sai cấu hình request/creds.
    return MISCONFIG, detail


def _check_token_file(token_file: str, timeout: int) -> tuple[str, str]:
    """Kiểm tra 1 file token (đọc + probe refresh) → (code, detail). Thuần."""
    data, read_code = _read_token_file(token_file)
    if read_code is not None:
        detail = (f"không tìm thấy {token_file}" if read_code == MISSING
                  else f"file token hỏng: {token_file}")
        return read_code, detail
    return _probe_refresh(data, timeout)


def check_channel(channel_key: str, timeout: int | None = None) -> TokenCheckResult:
    """Kiểm tra token của 1 kênh YouTube. Không alert, không ghi state (thuần)."""
    timeout = config.TOKEN_HEALTH_TIMEOUT if timeout is None else timeout
    channel = channels.get_channel(channel_key)
    token_file = resolve_token_file(channel_key)
    code, detail = _check_token_file(token_file, timeout)
    return TokenCheckResult(channel_key, channel["name"], token_file, code, detail)


def _youtube_channel_keys() -> list[str]:
    """Mọi kênh platform=youtube trong registry (source of truth)."""
    return list(channels.channels_for_platform("youtube").keys())


def check_all(channel_keys: list[str] | None = None,
              timeout: int | None = None) -> list[TokenCheckResult]:
    """Kiểm tra token của nhiều kênh YouTube.

    Probe được cache theo ĐƯỜNG DẪN token đã resolve, nên 2 kênh chưa cấu hình
    env (cùng fallback về YOUTUBE_TOKEN_FILE) không bị gọi mạng 2 lần.
    """
    timeout = config.TOKEN_HEALTH_TIMEOUT if timeout is None else timeout
    keys = channel_keys if channel_keys is not None else _youtube_channel_keys()
    results: list[TokenCheckResult] = []
    probe_cache: dict[str, tuple[str, str]] = {}  # token_file -> (code, detail)

    for key in keys:
        channel = channels.get_channel(key)
        token_file = resolve_token_file(key)

        if token_file not in probe_cache:
            probe_cache[token_file] = _check_token_file(token_file, timeout)
        code, detail = probe_cache[token_file]

        results.append(TokenCheckResult(key, channel["name"], token_file, code, detail))
    return results


_STATE_PREFIX = "token_health_transient:"


def _get_transient_count(channel_key: str) -> int:
    """Bộ đếm transient bền vững; DB chưa migrate 008 → 0 (degrade gracefully)."""
    try:
        from storage.pipeline_state import get_int
        return get_int(_STATE_PREFIX + channel_key, 0)
    except Exception as e:
        logger.warning("Không đọc được transient counter (%s): %s", channel_key, e)
        return 0


def _set_transient_count(channel_key: str, value: int) -> None:
    try:
        from storage.pipeline_state import set_int
        set_int(_STATE_PREFIX + channel_key, value)
    except Exception as e:
        logger.warning("Không ghi được transient counter (%s): %s", channel_key, e)


def _alert_message(res: TokenCheckResult) -> str | None:
    """Tin nhắn Telegram cho 1 kết quả (None = không alert)."""
    name, key, path = res.channel_name, res.channel_key, res.token_file
    reauth = (f"Cấp lại: cd content-pipeline && "
              f"python publisher/youtube_uploader.py --token-file {path}")

    if res.code == REVOKED:
        return (f"🔴 Token YouTube kênh '{name}' ({key}) đã bị THU HỒI/HẾT HẠN "
                f"(invalid_grant) — upload sẽ thất bại. {reauth}")
    if res.code == MISSING:
        return (f"🔴 Thiếu file token YouTube cho '{name}' ({key}): {path} — "
                f"upload sẽ thất bại. {reauth}")
    if res.code == NO_REFRESH_TOKEN:
        return (f"🔴 Token '{name}' ({key}) KHÔNG có refresh_token — sẽ chết khi "
                f"access token hết hạn. {reauth}")
    if res.code == UNREADABLE:
        return (f"🔴 File token '{name}' ({key}) hỏng/không đọc được: {path}. {reauth}")
    if res.code == MISCONFIG:
        return (f"🔴 Token '{name}' ({key}) lỗi cấu hình OAuth ({res.detail}) — "
                f"kiểm tra client_secret/file token. {reauth}")
    return None


def check_and_alert(channel_keys: list[str] | None = None,
                    timeout: int | None = None) -> list[TokenCheckResult]:
    """Kiểm tra + alert Telegram cho token chết. Trả toàn bộ kết quả.

    - Trạng thái ĐỊNH DANH xấu (revoked/missing/...) → alert ngay mỗi lần chạy
      (nhắc lại hằng ngày tới khi cấp lại token — giống staleness collector).
    - TRANSIENT → tăng bộ đếm; chỉ alert khi CHẠM ngưỡng
      TOKEN_HEALTH_TRANSIENT_ALERT_AFTER (một lần) để phát hiện "monitor không
      tới được Google" mà không spam khi Google chỉ chập chờn.
    - OK / bất kỳ kết quả định danh nào → reset bộ đếm transient.

    Best-effort: lỗi gửi Telegram được nuốt, không raise (như collector_health).
    """
    results = check_all(channel_keys, timeout=timeout)
    threshold = config.TOKEN_HEALTH_TRANSIENT_ALERT_AFTER

    def _send(text: str) -> None:
        try:
            from notifier.telegram_bot import send_alert
            send_alert(text)
        except Exception as e:
            logger.warning("Token-health alert send failed (non-fatal): %s", e)

    for res in results:
        if res.code == OK:
            logger.info("Token OK: %s (%s)", res.channel_name, res.channel_key)
            _set_transient_count(res.channel_key, 0)
            continue

        if res.code == TRANSIENT:
            count = _get_transient_count(res.channel_key) + 1
            _set_transient_count(res.channel_key, count)
            logger.warning("Token probe transient cho %s (%s), lần %d: %s",
                           res.channel_name, res.channel_key, count, res.detail)
            if threshold > 0 and count == threshold:
                _send(f"⚠️ Không kiểm tra được token YouTube '{res.channel_name}' "
                      f"({res.channel_key}) {count} lần liên tiếp — mạng/endpoint "
                      f"OAuth Google có vấn đề, hoặc cron token-health lỗi. "
                      f"({res.detail})")
            continue

        # Định danh xấu → reset transient (endpoint đã trả lời / lỗi cục bộ) + alert.
        _set_transient_count(res.channel_key, 0)
        logger.warning("Token %s cho %s (%s): %s",
                       res.code, res.channel_name, res.channel_key, res.detail)
        msg = _alert_message(res)
        if msg:
            _send(msg)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = check_and_alert()
    bad = [r for r in results if not r.healthy]
    print(f"Checked {len(results)} YouTube token(s); "
          f"{len(results) - len(bad)} OK, {len(bad)} có vấn đề.")
    for r in results:
        mark = "✅" if r.healthy else "❌"
        line = f"  {mark} {r.channel_key} ({r.channel_name}): {r.code}"
        if r.detail:
            line += f" — {r.detail}"
        print(line)
