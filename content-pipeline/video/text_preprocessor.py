from __future__ import annotations

"""
Text Preprocessor — Chuyển số thành chữ tiếng Việt trước khi gửi TTS.

Vấn đề: TTS engine đọc số như "100" sai (đọc từng chữ số thay vì "một trăm").
Giải pháp: Chuyển đổi trước khi gửi API.

Ví dụ:
    "100 người dùng"  → "một trăm người dùng"
    "tăng 1.5 lần"    → "tăng một phẩy năm lần"
    "năm 2024"        → "năm hai nghìn không trăm hai mươi tư"
    "50% người dùng"  → "năm mươi phần trăm người dùng"
    "5-7%"            → "năm đến bảy phần trăm"
    "10-15 phút"      → "mười đến mười lăm phút"
    "3-5 triệu"       → "ba đến năm triệu"
    "$10 mỗi tháng"   → "mười đô la mỗi tháng"
    "thứ 3 trong tuần"→ "thứ ba trong tuần"
"""

import logging
import re

logger = logging.getLogger(__name__)

try:
    from num2words import num2words as _num2words
    _HAS_NUM2WORDS = True
except ImportError:
    _HAS_NUM2WORDS = False
    logger.warning(
        "num2words not installed — dùng fallback nội bộ. "
        "Cài bằng: pip install num2words"
    )


# ---------------------------------------------------------------------------
# Fallback: bảng chuyển đổi nội bộ (dùng khi num2words chưa cài)
# ---------------------------------------------------------------------------

_ONES = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_TEENS = [
    "mười", "mười một", "mười hai", "mười ba", "mười bốn", "mười lăm",
    "mười sáu", "mười bảy", "mười tám", "mười chín",
]
_TENS = [
    "", "mười", "hai mươi", "ba mươi", "bốn mươi", "năm mươi",
    "sáu mươi", "bảy mươi", "tám mươi", "chín mươi",
]


def _int_to_vi_fallback(n: int) -> str:
    """Chuyển số nguyên sang tiếng Việt (fallback, không cần thư viện)."""
    if n < 0:
        return "âm " + _int_to_vi_fallback(-n)
    if n == 0:
        return "không"
    if n < 10:
        return _ONES[n]
    if n < 20:
        return _TEENS[n - 10]
    if n < 100:
        t, o = divmod(n, 10)
        if o == 0:
            return _TENS[t]
        if o == 5:
            return f"{_TENS[t]} lăm"
        if o == 1 and t > 1:
            return f"{_TENS[t]} mốt"
        return f"{_TENS[t]} {_ONES[o]}"
    if n < 1_000:
        h, r = divmod(n, 100)
        base = f"{_ONES[h]} trăm"
        if r == 0:
            return base
        if r < 10:
            return f"{base} lẻ {_ONES[r]}"
        return f"{base} {_int_to_vi_fallback(r)}"
    if n < 1_000_000:
        th, r = divmod(n, 1_000)
        base = f"{_int_to_vi_fallback(th)} nghìn"
        if r == 0:
            return base
        if r < 100:
            return f"{base} không trăm {_int_to_vi_fallback(r)}"
        return f"{base} {_int_to_vi_fallback(r)}"
    if n < 1_000_000_000:
        m, r = divmod(n, 1_000_000)
        base = f"{_int_to_vi_fallback(m)} triệu"
        if r == 0:
            return base
        return f"{base} {_int_to_vi_fallback(r)}"
    b, r = divmod(n, 1_000_000_000)
    base = f"{_int_to_vi_fallback(b)} tỷ"
    if r == 0:
        return base
    return f"{base} {_int_to_vi_fallback(r)}"


# ---------------------------------------------------------------------------
# Hàm chính
# ---------------------------------------------------------------------------

def _int_to_vi(n: int) -> str:
    if _HAS_NUM2WORDS:
        try:
            return _num2words(n, lang="vi")
        except Exception:
            pass
    return _int_to_vi_fallback(n)


def _decimal_to_vi(num_str: str) -> str:
    """'1.5' → 'một phẩy năm', đọc từng chữ số phần thập phân."""
    parts = num_str.split(".")
    int_words = _int_to_vi(int(parts[0]))
    if len(parts) == 1 or not parts[1]:
        return int_words
    dec_words = " ".join(_ONES[int(d)] for d in parts[1])
    return f"{int_words} phẩy {dec_words}"


def _num_str_to_vi(s: str) -> str:
    """Chuyển chuỗi số (nguyên hoặc thập phân) sang tiếng Việt."""
    s = s.replace(",", "")
    return _decimal_to_vi(s) if "." in s else _int_to_vi(int(s))


def preprocess_for_tts(text: str) -> str:
    """Chuyển số và ký hiệu đặc biệt sang chữ tiếng Việt để TTS đọc đúng.

    Thứ tự xử lý (cụ thể → tổng quát):
    1. Phạm vi phần trăm: 5-7% → năm đến bảy phần trăm
    2. Phạm vi số: 10-15 → mười đến mười lăm
    3. Tiền tệ USD: $10 → mười đô la
    4. Phần trăm đơn: 50% → năm mươi phần trăm
    5. Số thập phân: 1.5 → một phẩy năm
    6. Số có dấu phẩy nghìn: 1,000 → một nghìn
    7. Số nguyên còn lại: 100 → một trăm
    """
    if not text:
        return text

    # Số dùng trong range: nguyên hoặc thập phân, có thể có dấu phẩy nghìn
    _NUM = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"

    # 1. Phạm vi phần trăm: 5-7%, 1.5-2.5%
    def _range_pct(m: re.Match) -> str:
        try:
            w1 = _num_str_to_vi(m.group(1))
            w2 = _num_str_to_vi(m.group(2))
            return f"{w1} đến {w2} phần trăm"
        except (ValueError, IndexError):
            return m.group(0)

    text = re.sub(
        rf"(?<![a-zA-Z0-9])({_NUM})-({_NUM})%",
        _range_pct, text,
    )

    # 2. Phạm vi số: 10-15, 3-5, 1.5-2.5
    # Chỉ match khi số đứng đầu không liền sau chữ cái (tránh "v2-3")
    def _range_plain(m: re.Match) -> str:
        try:
            w1 = _num_str_to_vi(m.group(1))
            w2 = _num_str_to_vi(m.group(2))
            return f"{w1} đến {w2}"
        except (ValueError, IndexError):
            return m.group(0)

    text = re.sub(
        rf"(?<![a-zA-Z0-9])({_NUM})-({_NUM})(?![a-zA-Z0-9\.])",
        _range_plain, text,
    )

    # 3. Tiền tệ USD: $10, $1,000, $1.5
    def _usd(m: re.Match) -> str:
        try:
            return f"{_num_str_to_vi(m.group(1))} đô la"
        except (ValueError, IndexError):
            return m.group(0)

    text = re.sub(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)", _usd, text)

    # 4. Phần trăm đơn: 50%, 1.5%
    def _pct(m: re.Match) -> str:
        try:
            return f"{_num_str_to_vi(m.group(1))} phần trăm"
        except (ValueError, IndexError):
            return m.group(0)

    text = re.sub(r"([0-9][0-9,]*(?:\.[0-9]+)?)%", _pct, text)

    # 5. Số thập phân: 1.5, 3.14
    # Không match nếu liền sau chữ cái (tránh "v1.5") hoặc sau dấu chấm khác ("3.14.0")
    def _decimal(m: re.Match) -> str:
        try:
            return _decimal_to_vi(m.group(0))
        except (ValueError, IndexError):
            return m.group(0)

    text = re.sub(r"(?<![a-zA-Z0-9])[0-9]+\.[0-9]+(?![a-zA-Z0-9\.])", _decimal, text)

    # 6. Số có dấu phẩy nghìn: 1,000 / 1,000,000
    def _comma_num(m: re.Match) -> str:
        try:
            return _int_to_vi(int(m.group(0).replace(",", "")))
        except ValueError:
            return m.group(0)

    text = re.sub(r"(?<![a-zA-Z0-9])[0-9]{1,3}(?:,[0-9]{3})+(?![a-zA-Z0-9])", _comma_num, text)

    # 7. Số nguyên còn lại
    def _plain_int(m: re.Match) -> str:
        try:
            return _int_to_vi(int(m.group(0)))
        except ValueError:
            return m.group(0)

    text = re.sub(r"(?<![a-zA-Z0-9\.])[0-9]+(?![a-zA-Z0-9\.])", _plain_int, text)

    return text


# ---------------------------------------------------------------------------
# Test thủ công: python text_preprocessor.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    cases = [
        # (input, expected_substring)
        ("100 người dùng", "một trăm người dùng"),
        ("tăng 1.5 lần", "một phẩy năm"),
        ("năm 2024", "hai nghìn"),
        ("50% người dùng", "năm mươi phần trăm"),
        ("$10 mỗi tháng", "mười đô la"),
        ("tiết kiệm $1,000", "một nghìn đô la"),
        ("thứ 3 trong tuần", "thứ ba"),
        ("có 1,000,000 người", "một triệu"),
        ("tốc độ 3.14 lần", "ba phẩy một bốn"),
        # Range patterns
        ("5-7% tăng trưởng", "năm đến bảy phần trăm"),
        ("10-15 phút", "mười đến mười lăm"),
        ("3-5 triệu đồng", "ba đến năm triệu"),
        ("1.5-2.5 lần", "một phẩy năm đến hai phẩy năm"),
        # Numbers after hyphens are now converted
        ("GPT-4 là model", "GPT-bốn"),
    ]

    all_ok = True
    for text_in, expected in cases:
        result = preprocess_for_tts(text_in)
        ok = expected in result
        status = "OK" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"[{status}] '{text_in}' → '{result}'  (expect: '{expected}')")

    sys.exit(0 if all_ok else 1)
