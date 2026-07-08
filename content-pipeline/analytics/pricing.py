from __future__ import annotations

"""
Pricing overlay (Phase 6) — quy đổi token thô trong `cost_logs` ra USD.

Tách khỏi chỗ GHI log (storage/cost_logs.py, processors/ai_usage.py) một cách
có chủ đích: dữ liệu lịch sử lưu token thô (không bao giờ stale); tiền chỉ được
tính khi HIỂN THỊ, dùng bảng giá ở đây. Đổi giá = sửa 1 chỗ (hoặc override qua
env var `PRICE_<MODEL>_IN` / `_OUT` theo USD trên 1 TRIỆU token) và mọi báo cáo
tự tính lại — không cần migrate dữ liệu.

Đây cũng là lý do processors/ai_usage.py xưa nay từ chối nhét pricing vào chỗ
log token: giá thay đổi âm thầm, hardcode ở hot path sẽ lệch hoá đơn mà không
ai biết. Ở đây giá là 1 lớp overlay tường minh, dễ cập nhật.

Giá mặc định (USD / 1 triệu token) — CẬP NHẬT theo trang giá Anthropic khi giá
đổi. Kiểm tra: https://www.anthropic.com/pricing  (hoặc override bằng env var).
"""

import logging
import os

logger = logging.getLogger(__name__)

# USD trên 1 TRIỆU token (input, output). Chỉ là mặc định — override qua env.
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-5": (5.0, 25.0),
}


def _env_key(model: str) -> str:
    """Chuẩn hoá tên model thành hậu tố env var: claude-haiku-4-5 → CLAUDE_HAIKU_4_5."""
    return model.upper().replace("-", "_").replace(".", "_")


def rates_for(model: str) -> tuple[float, float] | None:
    """(giá_input, giá_output) USD/1M token cho `model`, hoặc None nếu chưa biết.

    Ưu tiên env override `PRICE_<MODEL>_IN` / `PRICE_<MODEL>_OUT`, rồi bảng
    mặc định. Match cả prefix (vd 'claude-haiku-4-5-20251001' khớp
    'claude-haiku-4-5') để id có hậu tố ngày vẫn tra được.
    """
    if not model:
        return None
    base = _env_key(model)
    env_in = os.getenv(f"PRICE_{base}_IN")
    env_out = os.getenv(f"PRICE_{base}_OUT")
    if env_in and env_out:
        try:
            return float(env_in), float(env_out)
        except ValueError:
            logger.warning("Bad PRICE_%s_* env value, ignoring", base)

    if model in _DEFAULT_PRICING:
        return _DEFAULT_PRICING[model]
    # prefix match cho id có hậu tố (vd -20251001)
    for known, rate in _DEFAULT_PRICING.items():
        if model.startswith(known):
            return rate
    return None


def cost_usd(model: str, input_tokens: int | None,
             output_tokens: int | None) -> float | None:
    """Chi phí USD cho 1 call. None nếu model chưa có giá (đừng đoán = 0)."""
    rates = rates_for(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    ti = input_tokens or 0
    to = output_tokens or 0
    return (ti * in_rate + to * out_rate) / 1_000_000


def summarize_costs(rows: list[dict]) -> dict:
    """Tổng hợp $ từ các dòng cost_logs thô (storage.cost_logs.rows_since()).

    Returns {
        "total_usd": float,
        "by_model": {model: {"calls", "input_tokens", "output_tokens", "usd"}},
        "by_service": {service: usd},
        "unpriced_models": [model, ...],   # có token nhưng chưa có giá
    }
    """
    by_model: dict[str, dict] = {}
    by_service: dict[str, float] = {}
    unpriced: set[str] = set()
    total = 0.0

    for row in rows:
        model = row.get("model") or "(unknown)"
        service = row.get("service") or "(unknown)"
        ti = row.get("input_tokens") or 0
        to = row.get("output_tokens") or 0

        entry = by_model.setdefault(
            model, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
        )
        entry["calls"] += 1
        entry["input_tokens"] += ti
        entry["output_tokens"] += to

        usd = cost_usd(model, ti, to)
        if usd is None:
            if ti or to:
                unpriced.add(model)
            usd = 0.0
        entry["usd"] += usd
        by_service[service] = by_service.get(service, 0.0) + usd
        total += usd

    return {
        "total_usd": round(total, 4),
        "by_model": by_model,
        "by_service": {k: round(v, 4) for k, v in by_service.items()},
        "unpriced_models": sorted(unpriced),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for model in _DEFAULT_PRICING:
        print(model, "→", rates_for(model),
              "| 1M in + 100k out =",
              cost_usd(model, 1_000_000, 100_000), "USD")
