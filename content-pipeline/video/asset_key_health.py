from __future__ import annotations

"""
Media asset API key health — follow-up cho issue #94 (giám sát credential).

token_health.py lo OAuth token của kênh YouTube; module này lo các **API key
TĨNH** của nhà cung cấp asset video mà pipeline phụ thuộc:

- **Pexels** (`PEXELS_API_KEY`) — nguồn b-roll nền cho CẢ hai track. Key hỏng/
  hết hạn không làm crash (pexels_downloader fallback về clip cache) nhưng **âm
  thầm** khiến mọi video mới dùng lại nền cũ → chất lượng tụt mà không ai biết
  (đúng loại "hỏng im lặng" mà #94 muốn chặn).
- **Replicate** (`REPLICATE_API_TOKEN`) — minh hoạ AI cho track Drama. TUỲ CHỌN:
  không cấu hình thì composer fallback gradient (image_generator trả None), nên
  key RỖNG = tính năng tắt hợp lệ → KHÔNG alert; chỉ khi đã đặt key mà key hỏng
  mới đáng báo.

Khác token YouTube (OAuth refresh) — đây là bearer key tĩnh, nên chỉ cần gọi 1
request xác thực nhẹ (200 = sống, 401/403 = key hỏng). Vẫn giữ đúng tinh thần
token_health: stdlib urllib + socket timeout fail-fast, phân biệt lỗi ĐỊNH DANH
(key hỏng/thiếu) với TẠM THỜI (timeout/5xx/429 — đếm bền vững, chỉ alert khi lặp
để "monitor không tới được provider" cũng lộ ra), best-effort không raise, KHÔNG
log giá trị key.

Chạy độc lập:  python -m video.asset_key_health
launchd:       launchd/com.ai5phut.asset-key-health.plist (08:10 hằng ngày)
Defense-in-depth: main.run_pipeline gọi best-effort (track AI dùng Pexels).
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

# Endpoint xác thực nhẹ nhất của mỗi provider (giữ đồng bộ với module gọi thật:
# video/pexels_downloader.py và video/image_generator.py).
_PEXELS_PROBE_URL = "https://api.pexels.com/videos/search?query=nature&per_page=1"
_REPLICATE_ACCOUNT_URL = "https://api.replicate.com/v1/account"

# Mã trạng thái.
OK = "ok"
INVALID = "invalid"        # 401/403 — key sai/hết hạn/thu hồi
BLOCKED = "blocked"        # 403 từ Cloudflare (error code 10xx) — key ĐÚNG, request bị WAF chặn (issue #97)
MISSING = "missing"        # key bắt buộc nhưng chưa cấu hình
DISABLED = "disabled"      # key tuỳ chọn chưa cấu hình → tính năng tắt (không alert)
TRANSIENT = "transient"    # timeout/mạng/5xx/429 — thử lại lần sau

# Cloudflare chặn client (vd User-Agent "Python-urllib/3.x" — issue #97) bằng
# 403 với body dạng text thuần "error code: 1010" HOẶC trang HTML block chứa
# markup `<span class="cf-error-code">1010</span>`. Bắt cả hai để phân biệt với
# 403 "key sai" thật — alert nói đúng sự thật, không xúi đi thay key vô ích.
_CF_BLOCK_RE = re.compile(
    rb"error code:?\s*(10\d\d)|cf-error-code[^>]*>\s*(10\d\d)",
    re.IGNORECASE,
)


def _cloudflare_block_code(err: urllib.error.HTTPError) -> str | None:
    """Mã chặn Cloudflare ("1010"…) từ body lỗi; None nếu không phải WAF block."""
    try:
        # Trang HTML block của Cloudflare dài — đọc đủ sâu để thấy error code.
        body = err.read(4096)
    except Exception:
        return None
    m = _CF_BLOCK_RE.search(body or b"")
    if not m:
        return None
    return next(g for g in m.groups() if g).decode()


class KeyCheckResult:
    """Kết quả kiểm tra 1 API key."""

    __slots__ = ("provider", "code", "detail")

    def __init__(self, provider, code, detail=""):
        self.provider = provider
        self.code = code
        self.detail = detail

    @property
    def healthy(self) -> bool:
        return self.code in (OK, DISABLED)

    def __repr__(self) -> str:
        return (f"KeyCheckResult({self.provider}, {self.code}"
                + (f", {self.detail!r}" if self.detail else "") + ")")


def _probe(url: str, headers: dict, timeout: int) -> tuple[str, str]:
    """GET `url` với header xác thực → (code, detail). Chỉ stdlib, HTTPS verify.

    KHÔNG bao giờ đưa giá trị key vào detail (headers chỉ dùng để gửi).
    User-Agent bắt buộc: urllib mặc định gửi "Python-urllib/3.x" và Cloudflare
    chặn nó bằng 403 error code 1010 (issue #97) — probe sẽ báo key hỏng oan.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": config.HTTP_USER_AGENT, **headers}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(1)
        return OK, ""
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            cf = _cloudflare_block_code(e)
            if cf:
                return BLOCKED, (f"HTTP {e.code} — Cloudflare chặn request "
                                 f"(error code {cf}), KHÔNG phải lỗi key")
            return INVALID, f"HTTP {e.code} — key bị từ chối"
        if e.code == 429 or e.code >= 500:
            return TRANSIENT, f"HTTP {e.code}"
        # 4xx khác (400 query...) coi như tạm thời/không kết luận key hỏng.
        return TRANSIENT, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return TRANSIENT, f"không kết nối được: {e}"


def check_pexels(timeout: int | None = None) -> KeyCheckResult:
    """Kiểm tra `PEXELS_API_KEY`. Key rỗng = MISSING (Pexels là nguồn nền chính)."""
    timeout = config.ASSET_KEY_HEALTH_TIMEOUT if timeout is None else timeout
    key = getattr(config, "PEXELS_API_KEY", "") or ""
    if not key:
        return KeyCheckResult("pexels", MISSING,
                              "PEXELS_API_KEY chưa cấu hình — video dùng nền cache cũ")
    # Pexels dùng raw key trong header Authorization (không phải Bearer).
    code, detail = _probe(_PEXELS_PROBE_URL, {"Authorization": key}, timeout)
    return KeyCheckResult("pexels", code, detail)


def check_replicate(timeout: int | None = None) -> KeyCheckResult:
    """Kiểm tra `REPLICATE_API_TOKEN`. Key rỗng = DISABLED (tuỳ chọn — không alert)."""
    timeout = config.ASSET_KEY_HEALTH_TIMEOUT if timeout is None else timeout
    token = getattr(config, "REPLICATE_API_TOKEN", "") or ""
    if not token:
        return KeyCheckResult("replicate", DISABLED,
                              "REPLICATE_API_TOKEN chưa đặt — minh hoạ AI tắt (composer fallback gradient)")
    code, detail = _probe(_REPLICATE_ACCOUNT_URL,
                          {"Authorization": f"Bearer {token}"}, timeout)
    return KeyCheckResult("replicate", code, detail)


_CHECKS = {"pexels": check_pexels, "replicate": check_replicate}


def check_all(providers: list[str] | None = None,
              timeout: int | None = None) -> list[KeyCheckResult]:
    """Kiểm tra các provider (mặc định: tất cả)."""
    names = providers if providers is not None else list(_CHECKS.keys())
    return [_CHECKS[name](timeout=timeout) for name in names if name in _CHECKS]


_STATE_PREFIX = "asset_key_transient:"


def _get_transient_count(provider: str) -> int:
    """Bộ đếm transient bền vững; DB chưa migrate 008 → 0 (degrade gracefully)."""
    try:
        from storage.pipeline_state import get_int
        return get_int(_STATE_PREFIX + provider, 0)
    except Exception as e:
        logger.warning("Không đọc được transient counter (%s): %s", provider, e)
        return 0


def _set_transient_count(provider: str, value: int) -> None:
    try:
        from storage.pipeline_state import set_int
        set_int(_STATE_PREFIX + provider, value)
    except Exception as e:
        logger.warning("Không ghi được transient counter (%s): %s", provider, e)


def _alert_message(res: KeyCheckResult) -> str | None:
    """Tin nhắn Telegram cho 1 kết quả (None = không alert)."""
    if res.code == BLOCKED:
        impact = ("video mới sẽ dùng lại nền cache cũ"
                  if res.provider == "pexels"
                  else "minh hoạ AI (Drama) sẽ fallback gradient")
        return (f"🔴 Request tới {res.provider} bị Cloudflare CHẶN ({res.detail}). "
                f"Key KHÔNG sai — đừng thay key. {impact}. "
                "Kiểm tra HTTP_USER_AGENT trong .env hoặc IP đang bị flag (issue #97).")
    if res.provider == "pexels":
        if res.code == INVALID:
            return ("🔴 PEXELS_API_KEY bị từ chối "
                    f"({res.detail}) — video mới sẽ dùng lại nền cache cũ. "
                    "Cấp key mới tại https://www.pexels.com/api/ rồi cập nhật .env.")
        if res.code == MISSING:
            return ("🟡 PEXELS_API_KEY chưa cấu hình — video dùng nền cache cũ, "
                    "chất lượng nền giảm. Lấy key miễn phí tại "
                    "https://www.pexels.com/api/ và đặt vào .env.")
    if res.provider == "replicate" and res.code == INVALID:
        return ("🔴 REPLICATE_API_TOKEN bị từ chối "
                f"({res.detail}) — minh hoạ AI (Drama) sẽ fallback gradient. "
                "Lấy token tại https://replicate.com/account/api-tokens rồi cập nhật .env.")
    return None


def check_and_alert(providers: list[str] | None = None,
                    timeout: int | None = None) -> list[KeyCheckResult]:
    """Kiểm tra + alert Telegram cho key hỏng/thiếu. Trả toàn bộ kết quả.

    - INVALID/MISSING/BLOCKED → alert ngay mỗi lần chạy (nhắc tới khi sửa).
    - TRANSIENT → tăng bộ đếm; chỉ alert khi CHẠM ngưỡng
      ASSET_KEY_HEALTH_TRANSIENT_ALERT_AFTER (một lần) để "monitor không tới được
      provider" cũng lộ ra, không spam khi provider chập chờn.
    - OK / DISABLED → reset bộ đếm, không alert.

    Best-effort: lỗi gửi Telegram được nuốt, không raise (như token_health).
    """
    results = check_all(providers, timeout=timeout)
    threshold = config.ASSET_KEY_HEALTH_TRANSIENT_ALERT_AFTER

    def _send(text: str) -> None:
        try:
            from notifier.telegram_bot import send_alert
            send_alert(text)
        except Exception as e:
            logger.warning("Asset-key alert send failed (non-fatal): %s", e)

    for res in results:
        if res.code in (OK, DISABLED):
            logger.info("Asset key %s: %s", res.provider, res.code)
            _set_transient_count(res.provider, 0)
            continue

        if res.code == TRANSIENT:
            count = _get_transient_count(res.provider) + 1
            _set_transient_count(res.provider, count)
            logger.warning("Asset key %s transient lần %d: %s",
                           res.provider, count, res.detail)
            if threshold > 0 and count == threshold:
                _send(f"⚠️ Không kiểm tra được API key {res.provider} {count} lần "
                      f"liên tiếp — mạng/endpoint provider có vấn đề, hoặc cron "
                      f"asset-key-health lỗi. ({res.detail})")
            continue

        # INVALID / MISSING / BLOCKED → reset transient + alert.
        _set_transient_count(res.provider, 0)
        logger.warning("Asset key %s %s: %s", res.provider, res.code, res.detail)
        msg = _alert_message(res)
        if msg:
            _send(msg)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = check_and_alert()
    print(f"Checked {len(results)} media asset API key(s).")
    for r in results:
        mark = "✅" if r.healthy else "❌"
        line = f"  {mark} {r.provider}: {r.code}"
        if r.detail:
            line += f" — {r.detail}"
        print(line)
