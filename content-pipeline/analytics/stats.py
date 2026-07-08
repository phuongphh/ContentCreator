from __future__ import annotations

"""
Thống kê tối thiểu, KHÔNG phụ thuộc scipy/numpy (Phase 6).

Chỉ cần cho experiment_compare: Welch's t-test 2 mẫu (phương sai không bằng
nhau) + p-value 2 phía. p-value tính qua hàm beta không hoàn chỉnh
(regularized incomplete beta, continued fraction — Numerical Recipes) để ra
CDF phân phối Student-t chính xác, thay vì kéo cả scipy vào chỉ để chạy 1 test.

Cảnh báo diễn giải (phase-6-detailed.md §5): p-value KHÔNG thay cho quy tắc
"≥10 sample/arm mới quyết định". Mẫu nhỏ → p dễ ra tín hiệu giả. Hàm trả cả
n để caller tự chặn.
"""

import math
from typing import Sequence


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _variance(xs: Sequence[float]) -> float:
    """Phương sai mẫu (chia n-1). 0 nếu < 2 phần tử."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction cho hàm beta không hoàn chỉnh (Numerical Recipes)."""
    MAXIT, EPS, FPMIN = 200, 3.0e-12, 1.0e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m_i in range(1, MAXIT + 1):
        m2 = 2 * m_i
        aa = m_i * (b - m_i) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m_i) * (qab + m_i) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_sf_two_sided(t: float, df: float) -> float:
    """p-value 2 phía cho thống kê t với `df` bậc tự do."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    return _betai(df / 2.0, 0.5, x)


def welch_ttest(a: Sequence[float], b: Sequence[float]) -> dict:
    """Welch's t-test 2 mẫu độc lập.

    Returns {"t": float|None, "df": float|None, "p_value": float|None,
             "mean_a", "mean_b", "n_a", "n_b"}. t/df/p = None nếu không đủ dữ
             liệu (mỗi nhóm cần ≥2, và tổng phương sai > 0).
    """
    na, nb = len(a), len(b)
    out = {
        "mean_a": mean(a) if na else None,
        "mean_b": mean(b) if nb else None,
        "n_a": na, "n_b": nb,
        "t": None, "df": None, "p_value": None,
    }
    if na < 2 or nb < 2:
        return out
    va, vb = _variance(a), _variance(b)
    sa, sb = va / na, vb / nb
    denom = sa + sb
    if denom <= 0:
        return out  # cả 2 nhóm không phương sai → t vô định
    t = (mean(a) - mean(b)) / math.sqrt(denom)
    df = denom * denom / ((sa * sa) / (na - 1) + (sb * sb) / (nb - 1))
    out.update({"t": t, "df": df, "p_value": _t_sf_two_sided(t, df)})
    return out
