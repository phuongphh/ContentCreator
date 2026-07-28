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

Issue #109 — cảnh báo TRƯỚC khi token chết theo lịch:
Probe chỉ trả lời "token còn sống LÚC NÀY". Với OAuth consent screen ở chế độ
"Testing", Google cho refresh token sống đúng 7 ngày kể từ lúc mint, và nó chết
vào ĐÚNG GIỜ được cấp — nên probe 08:00 báo OK rồi upload 12:00 chết vì
invalid_grant là hành vi bình thường, KHÔNG phải probe sai (đây là hiểu lầm
trong mô tả issue #109: monitor vẫn luôn refresh thật, xem `_probe_refresh`).
Khe mù check-rồi-mới-dùng không thể đóng bằng cách probe sớm hơn, nên ta theo
dõi TUỔI của refresh_token và cảnh báo trước hạn `YOUTUBE_TOKEN_WARN_BEFORE_HOURS`.
Tuổi đo từ lần ĐẦU monitor nhìn thấy chính refresh_token đó (seed bằng mtime của
file token), lưu ở pipeline_state dưới dạng **hash** — không bao giờ lưu token.
Đổi app sang "In production" → đặt YOUTUBE_TOKEN_TTL_DAYS=0 để tắt cảnh báo này.

Chạy độc lập:  python -m publisher.token_health
launchd:       launchd/com.ai5phut.token-health.plist (08:00 + 11:30 hằng ngày —
               lần 11:30 đặt ngay trước slot đăng 12:00, xem issue #109)
Defense-in-depth: main.run_pipeline gọi best-effort như launchd_status.
"""

import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
import channels
from publisher.youtube_uploader import (
    resolve_token_file, _has_required_scopes, SCOPES,
)

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
UNCONFIGURED = "unconfigured"       # kênh chưa có token RIÊNG (dùng chung file — #95 review)
MISSING_SCOPES = "missing_scopes"   # refresh OK nhưng thiếu scope uploader cần (#95 review)
TRANSIENT = "transient"             # timeout/mạng/5xx/429 — thử lại lần sau


class TokenCheckResult:
    """Kết quả kiểm tra 1 kênh.

    `warning` tách khỏi `code` có chủ đích (issue #109): "token SẮP hết hạn" là
    lời khuyên chứ không phải trạng thái hỏng — token vẫn dùng được lúc này, nên
    `healthy` vẫn True và mọi caller cũ (webui/health, __main__) không đổi nghĩa.
    """

    __slots__ = ("channel_key", "channel_name", "token_file", "code", "detail",
                 "warning")

    def __init__(self, channel_key, channel_name, token_file, code, detail="",
                 warning=None):
        self.channel_key = channel_key
        self.channel_name = channel_name
        self.token_file = token_file
        self.code = code
        self.detail = detail
        self.warning = warning

    @property
    def healthy(self) -> bool:
        return self.code == OK

    def __repr__(self) -> str:
        return (f"TokenCheckResult({self.channel_key}, {self.code}"
                + (f", {self.detail!r}" if self.detail else "")
                + (f", warning={self.warning!r}" if self.warning else "") + ")")


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


# --- Tuổi refresh_token → cảnh báo trước khi hết hạn theo lịch (issue #109) ---

_MINTED_PREFIX = "token_health_minted:"          # key → "<fingerprint>|<iso>"
_EXPIRY_WARNED_PREFIX = "token_health_warned:"   # key → "<fingerprint>|<YYYY-MM-DD>"


def _fingerprint(refresh_token: str) -> str:
    """Định danh KHÔNG thể đảo ngược của 1 refresh_token (không bao giờ lưu token).

    Đủ để nhận ra "vẫn token cũ" hay "đã cấp lại token mới" — chỉ cần vậy để đo
    tuổi; 16 hex đầu của sha256 là quá đủ cho ~vài token, mà không giữ bí mật.
    """
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:16]


def _first_seen(channel_key: str, fingerprint: str, token_file: str,
                now: datetime) -> datetime | None:
    """Lần đầu monitor thấy refresh_token này (mốc ước lượng thời điểm cấp).

    Token mới (fingerprint đổi = vừa cấp lại) thì mốc được ghi lại từ đầu. Lần
    quan sát ĐẦU TIÊN sau khi deploy chưa có state → seed bằng mtime file token
    (mtime ≥ lúc mint vì uploader ghi đè file mỗi lần refresh access token, nên
    tuổi ước lượng chỉ có thể THẤP hơn thật → cảnh báo có thể muộn một vòng
    token, không bao giờ báo động giả). Không đọc/ghi được state → None (tắt êm).
    """
    key = _MINTED_PREFIX + channel_key
    try:
        from storage.pipeline_state import get_state, set_state
    except Exception as e:
        logger.warning("Không đọc được pipeline_state cho tuổi token: %s", e)
        return None

    try:
        raw = get_state(key)
        if raw:
            saved_fp, _, saved_ts = raw.partition("|")
            if saved_fp == fingerprint:
                try:
                    return datetime.fromisoformat(saved_ts)
                except ValueError:
                    pass  # state hỏng → ghi lại (self-heal, như get_int)

        seed = now
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(token_file))
            seed = min(seed, mtime)
        except OSError:
            pass
        set_state(key, f"{fingerprint}|{seed.isoformat(timespec='seconds')}")
        return seed
    except Exception as e:
        logger.warning("Không theo dõi được tuổi token %s: %s", channel_key, e)
        return None


def _expiry_warning(channel_key: str, token: dict, token_file: str,
                    now: datetime | None = None) -> str | None:
    """Cảnh báo "token sắp hết hạn theo lịch", hoặc None nếu chưa tới ngưỡng."""
    ttl_days = config.YOUTUBE_TOKEN_TTL_DAYS
    if ttl_days <= 0:  # app đã "In production" → token không hết hạn theo lịch
        return None
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return None  # đã có mã NO_REFRESH_TOKEN lo việc này

    now = now or datetime.now()
    seen = _first_seen(channel_key, _fingerprint(refresh_token), token_file, now)
    if seen is None:
        return None

    expires_at = seen + timedelta(days=ttl_days)
    remaining_h = (expires_at - now).total_seconds() / 3600
    if remaining_h > config.YOUTUBE_TOKEN_WARN_BEFORE_HOURS:
        return None
    if remaining_h <= 0:
        return (f"token đã quá hạn ước lượng {ttl_days:g} ngày "
                f"(cấp khoảng {seen:%d/%m %H:%M}) — có thể chết bất cứ lúc nào")
    return (f"còn ~{remaining_h:.0f} giờ là hết hạn theo lịch "
            f"({expires_at:%d/%m %H:%M}, TTL {ttl_days:g} ngày kể từ "
            f"{seen:%d/%m %H:%M})")


def _expiry_stamp(channel_key: str, now: datetime) -> str:
    """Mốc dedupe cảnh báo: `<fingerprint>|<mint>|<ngày>`.

    Lấy từ chính state tuổi token đã ghi nên cấp token MỚI là được cảnh báo lại
    ngay, không phải đợi sang ngày; đồng thời không phải đọc lại file token và
    không có bí mật nào đi vào state.
    """
    from storage.pipeline_state import get_state
    return f"{get_state(_MINTED_PREFIX + channel_key) or '?'}|{now:%Y-%m-%d}"


def _expiry_warning_pending(channel_key: str,
                            now: datetime | None = None) -> bool:
    """True nếu HÔM NAY chưa gửi được cảnh báo hết hạn cho token này.

    Cảnh báo lặp lại HẰNG NGÀY tới khi cấp lại token, nhưng 3 lần chạy/ngày
    (07:00 ké pipeline, 08:00 + 11:30 cron) không thành 3 tin nhắn.

    Tách khỏi `_record_expiry_warning` để chỉ ghi mốc SAU KHI gửi thành công
    (review Codex PR #110): `send_alert` trả False khi Telegram lỗi/chưa cấu
    hình, mà cảnh báo này thường chỉ có 1-2 cơ hội trước khi token chết — ghi
    mốc trước khi gửi sẽ khiến một lần 08:00 hỏng nuốt luôn lần 11:30, đúng cái
    tin nhắn cuối cùng còn kịp cứu video.
    """
    now = now or datetime.now()
    try:
        from storage.pipeline_state import get_state
        return get_state(_EXPIRY_WARNED_PREFIX + channel_key) != _expiry_stamp(
            channel_key, now)
    except Exception as e:
        # Không đọc được state → cứ gửi (thà nhắc thừa còn hơn im lặng để token
        # chết), cùng tinh thần degrade êm của phần còn lại module này.
        logger.warning("Không đọc được mốc cảnh báo hết hạn (%s): %s", channel_key, e)
        return True


def _record_expiry_warning(channel_key: str, now: datetime | None = None) -> None:
    """Ghi mốc "đã gửi cảnh báo hôm nay" — chỉ gọi sau khi gửi THÀNH CÔNG."""
    now = now or datetime.now()
    try:
        from storage.pipeline_state import set_state
        set_state(_EXPIRY_WARNED_PREFIX + channel_key, _expiry_stamp(channel_key, now))
    except Exception as e:
        logger.warning("Không ghi được mốc cảnh báo hết hạn (%s): %s", channel_key, e)


def _check_token_file(token_file: str, timeout: int) -> tuple[str, str, dict | None]:
    """Kiểm tra 1 file token (đọc + probe refresh + scope) → (code, detail, data).

    Thuần (không alert). Trả kèm nội dung file token đã đọc để tầng trên tính
    cảnh báo tuổi token (issue #109) mà không phải đọc file lần hai — một nguồn
    dữ liệu duy nhất cho cả probe lẫn cảnh báo.
    """
    data, read_code = _read_token_file(token_file)
    if read_code is not None:
        detail = (f"không tìm thấy {token_file}" if read_code == MISSING
                  else f"file token hỏng: {token_file}")
        return read_code, detail, None

    code, detail = _probe_refresh(data, timeout)
    if code != OK:
        return code, detail, data

    # Refresh được nhưng thiếu scope uploader cần (#95 review, line 143): token
    # cũ predate youtube.force-ssl vẫn refresh OK, nhưng youtube_uploader loại bỏ
    # nó (_has_required_scopes) rồi kích interactive OAuth — trong launchd headless
    # sẽ treo/lỗi. Bắt SỚM ở đây thay vì đợi tới giờ upload.
    if not _has_required_scopes(data.get("scopes")):
        have = data.get("scopes") or []
        missing = [s for s in SCOPES if s not in set(have)]
        return MISSING_SCOPES, f"thiếu scope: {', '.join(missing) or SCOPES}", data
    return OK, "", data


def check_channel(channel_key: str, timeout: int | None = None) -> TokenCheckResult:
    """Kiểm tra token của 1 kênh YouTube. Không alert (nhưng có ghi mốc tuổi
    token vào pipeline_state để cảnh báo trước hạn — issue #109)."""
    timeout = config.TOKEN_HEALTH_TIMEOUT if timeout is None else timeout
    channel = channels.get_channel(channel_key)
    token_file = resolve_token_file(channel_key)
    code, detail, data = _check_token_file(token_file, timeout)
    warning = (_expiry_warning(channel_key, data, token_file)
               if code == OK and data else None)
    return TokenCheckResult(channel_key, channel["name"], token_file, code, detail,
                            warning)


def _youtube_channel_keys() -> list[str]:
    """Mọi kênh platform=youtube trong registry (source of truth)."""
    return list(channels.channels_for_platform("youtube").keys())


def check_all(channel_keys: list[str] | None = None,
              timeout: int | None = None) -> list[TokenCheckResult]:
    """Kiểm tra token của nhiều kênh YouTube.

    Phát hiện COLLISION (#95 review, line 216): nếu 2+ kênh YouTube resolve về
    CÙNG một file token — vì env riêng (vd `YOUTUBE_DRAMA_TOKEN`) rỗng nên
    `resolve_token_file` fallback về `YOUTUBE_TOKEN_FILE` — thì kênh đó CHƯA có
    token riêng. Một file token chỉ uỷ quyền cho MỘT tài khoản Google/kênh, nên
    dùng chung = misconfig: upload sẽ lên sai kênh hoặc lỗi. Báo `unconfigured`
    thay vì im lặng để token AI hợp lệ khiến cả drama "xanh" (chính blind spot
    monitor này sinh ra để đóng). Kênh có path RIÊNG được probe bình thường
    (cache theo path — path riêng nên không gọi mạng trùng).

    Không alert, nhưng CÓ ghi mốc tuổi token vào pipeline_state (issue #109) để
    lần chạy sau biết token đã sống bao lâu.
    """
    timeout = config.TOKEN_HEALTH_TIMEOUT if timeout is None else timeout
    keys = channel_keys if channel_keys is not None else _youtube_channel_keys()

    # path → các kênh cùng resolve về đó (để phát hiện dùng chung).
    resolved: list[tuple[str, str]] = [(k, resolve_token_file(k)) for k in keys]
    path_owners: dict[str, list[str]] = {}
    for key, path in resolved:
        path_owners.setdefault(path, []).append(key)

    results: list[TokenCheckResult] = []
    # token_file -> (code, detail, token data) — probe 1 lần cho mỗi path.
    probe_cache: dict[str, tuple[str, str, dict | None]] = {}

    for key, token_file in resolved:
        channel = channels.get_channel(key)
        sharing = [k for k in path_owners[token_file] if k != key]
        warning = None
        if sharing:
            code, detail = (UNCONFIGURED,
                            f"dùng chung file token {token_file} với "
                            f"{', '.join(sharing)} — kênh này chưa có token riêng")
        else:
            if token_file not in probe_cache:
                probe_cache[token_file] = _check_token_file(token_file, timeout)
            code, detail, data = probe_cache[token_file]
            # Cảnh báo tuổi tính THEO KÊNH (mốc lưu theo channel_key) dù dữ liệu
            # token dùng chung cache theo path — issue #109.
            if code == OK and data:
                warning = _expiry_warning(key, data, token_file)

        results.append(TokenCheckResult(key, channel["name"], token_file, code,
                                        detail, warning))
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


def reauth_command(token_file: str) -> str:
    """Câu lệnh cấp lại token cho 1 file token — MỘT nguồn duy nhất.

    --force-reauth: bỏ token cũ (thu hồi/thiếu scope) rồi chạy flow OAuth mới —
    cần thiết vì rerun thường sẽ refresh() token cũ và raise invalid_grant TRƯỚC
    khi mở browser (#95 review, line 251). File chưa tồn tại thì flag này vô hại.

    Dùng chung với scheduler (issue #109) để alert lúc upload chết và alert của
    monitor hướng dẫn y hệt nhau — người vận hành không phải nhớ 2 câu lệnh.
    """
    return (f"Cấp lại: cd content-pipeline && "
            f"python publisher/youtube_uploader.py --token-file {token_file} "
            f"--force-reauth")


def is_auth_error(exc: BaseException) -> bool:
    """True nếu exception là lỗi TOKEN OAuth (không phải lỗi mạng/upload).

    Dùng ở scheduler để phân biệt "chưa gửi byte nào lên YouTube, retry an toàn"
    với mọi lỗi khác. google-auth raise RefreshError khi token endpoint từ chối
    refresh_token (invalid_grant = thu hồi/hết hạn — đúng ca issue #109); lỗi
    mạng thuần là TransportError nên KHÔNG lọt vào đây.

    Không import google-auth ở top-level: module này chạy được cả khi thiếu
    dependency (cùng lý do youtube_uploader import lười).
    """
    try:
        from google.auth.exceptions import RefreshError
        if isinstance(exc, RefreshError):
            return True
    except ImportError:
        pass
    # Fallback theo nội dung: lỗi có thể đã bị bọc lại thành RuntimeError/chuỗi
    # (vd đi qua ranh giới process hay thư viện khác).
    text = f"{type(exc).__name__}: {exc}".lower()
    return "invalid_grant" in text or "refresherror" in text


def auth_failure_alert(channel_key: str, detail: str, extra: str = "") -> str:
    """Tin nhắn Telegram khi upload chết vì token (scheduler gọi) — issue #109."""
    try:
        name = channels.get_channel(channel_key)["name"]
    except ValueError:
        name = channel_key
    token_file = resolve_token_file(channel_key)
    msg = (f"🔴 Token YouTube kênh '{name}' ({channel_key}) chết giữa chừng — "
           f"upload thất bại (invalid_grant).\n{detail}\n"
           f"{reauth_command(token_file)}")
    return f"{msg}\n{extra}" if extra else msg


def _alert_message(res: TokenCheckResult) -> str | None:
    """Tin nhắn Telegram cho 1 kết quả (None = không alert)."""
    name, key, path = res.channel_name, res.channel_key, res.token_file
    reauth = reauth_command(path)

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
    if res.code == MISSING_SCOPES:
        return (f"🔴 Token '{name}' ({key}) {res.detail} — uploader sẽ kích "
                f"interactive OAuth giữa lúc upload (treo trong launchd). {reauth}")
    if res.code == UNCONFIGURED:
        env = channels.get_channel(key)["oauth_token_env"]
        return (f"🔴 Kênh '{name}' ({key}) {res.detail}. Đặt {env} trỏ tới file "
                f"token RIÊNG trong .env rồi cấp token cho kênh này — xem "
                f"docs/current/oauth-setup.md §1.4.")
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

    def _send(text: str) -> bool:
        """Gửi Telegram; True nếu tin nhắn thực sự tới nơi.

        `send_alert` trả False (không raise) khi thiếu credential hoặc API lỗi —
        caller cần biết để không ghi mốc dedupe cho một tin chưa hề gửi được
        (review Codex PR #110).
        """
        try:
            from notifier.telegram_bot import send_alert
            return bool(send_alert(text))
        except Exception as e:
            logger.warning("Token-health alert send failed (non-fatal): %s", e)
            return False

    for res in results:
        if res.code == OK:
            logger.info("Token OK: %s (%s)%s", res.channel_name, res.channel_key,
                        f" — ⚠️ {res.warning}" if res.warning else "")
            _set_transient_count(res.channel_key, 0)
            if res.warning and _expiry_warning_pending(res.channel_key):
                # Token còn sống nhưng sắp hết hạn THEO LỊCH: đây là lớp duy
                # nhất đóng được khe mù "probe 08:00 OK → upload 12:00 chết"
                # (issue #109). Cấp lại trước slot đăng là xong, không mất video.
                sent = _send(f"⚠️ Token YouTube '{res.channel_name}' "
                             f"({res.channel_key}) {res.warning}. Cấp lại TRƯỚC "
                             f"giờ đăng để video không kẹt.\n"
                             f"{reauth_command(res.token_file)}\n"
                             f"(Hết cảnh báo này vĩnh viễn: đưa OAuth app sang 'In "
                             f"production' rồi đặt YOUTUBE_TOKEN_TTL_DAYS=0 — xem "
                             f"docs/current/oauth-setup.md §1.5.)")
                if sent:
                    _record_expiry_warning(res.channel_key)
                else:
                    # Gửi hỏng → KHÔNG ghi mốc, để lần chạy kế tiếp trong ngày
                    # (11:30) thử lại — đó có thể là tin nhắn cuối còn kịp.
                    logger.warning("Chưa gửi được cảnh báo hết hạn cho %s — sẽ "
                                   "thử lại lần chạy sau", res.channel_key)
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
        mark = "⚠️" if (r.healthy and r.warning) else ("✅" if r.healthy else "❌")
        line = f"  {mark} {r.channel_key} ({r.channel_name}): {r.code}"
        if r.detail:
            line += f" — {r.detail}"
        if r.warning:
            line += f" — SẮP HẾT HẠN: {r.warning}"
        print(line)
