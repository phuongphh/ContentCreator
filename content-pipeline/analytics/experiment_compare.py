from __future__ import annotations

"""
A/B experiment helper (Phase 6 EPIC #6.3) — so sánh các nhánh (arm) của 1 thí
nghiệm gắn trên video (thumbnail style / hook variant / độ dài) theo số liệu
thực từ `video_metrics`.

Khác processors/ab_harness.py (A/B prompt version ở tầng story, so mean
heuristic_score) — module này ở tầng VIDEO, so metric platform thật (views,
retention...) sau khi đăng.

Nguyên tắc cứng (phase-6-detailed.md §5): đừng kết luận khi mẫu nhỏ. Hàm trả
`enough_samples` theo `min_samples` (mặc định 5/arm cho acceptance criteria,
nhưng khuyến nghị ≥10 trước khi thực sự cắt format). p-value chỉ là tham khảo
bổ sung, không thay quy tắc mẫu.
"""

import logging
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_videos_by_experiment
from storage import video_metrics
from analytics.stats import welch_ttest, mean

logger = logging.getLogger(__name__)

DEFAULT_MIN_SAMPLES = 5
# Ngưỡng khuyến nghị để thật sự ra quyết định cắt/nhân format.
RECOMMENDED_MIN_SAMPLES = 10


# Metric cộng dồn được giữa các platform (đếm tuyệt đối). Các metric còn lại là
# TỶ LỆ / thời lượng — cộng chúng lại là sai (55% YT + 40% TikTok ≠ 95%), nên
# lấy trung bình giữa các platform.
_ADDITIVE_METRICS = {"views", "likes", "comments", "shares", "watch_time_minutes"}


def _metric_by_video_id(metric: str) -> dict[int, float]:
    """{video_id: giá trị metric mới nhất} cho mọi video có snapshot.

    Một video có thể có snapshot ở nhiều platform (YouTube + TikTok). Với metric
    đếm (views...) cộng dồn; với metric tỷ lệ/thời lượng (retention...) lấy
    trung bình — cộng tỷ lệ giữa 2 platform sẽ bóp méo mean của arm.
    """
    collected: dict[int, list[float]] = {}
    for row in video_metrics.latest_per_video():
        vid = row.get("video_id")
        val = row.get(metric)
        if vid is not None and val is not None:
            collected.setdefault(vid, []).append(val)
    if metric in _ADDITIVE_METRICS:
        return {vid: sum(vals) for vid, vals in collected.items()}
    return {vid: sum(vals) / len(vals) for vid, vals in collected.items()}


def compare_arms(experiment_id: str, metric: str = "views",
                 min_samples: int = DEFAULT_MIN_SAMPLES) -> dict:
    """So sánh các arm của `experiment_id` theo `metric`.

    Returns:
        {
          "experiment_id", "metric",
          "arms": {arm: {"n": int, "mean": float, "values": [...]}},
          "better": arm|"tie"|None,
          "delta": float|None,             # mean(better) - mean(other), chỉ khi 2 arm
          "p_value": float|None, "t": ..., "df": ...,   # chỉ khi đúng 2 arm đủ mẫu
          "enough_samples": bool,          # mọi arm >= min_samples
          "recommended_samples_met": bool, # mọi arm >= RECOMMENDED_MIN_SAMPLES
          "note": str,
        }
    """
    videos = get_videos_by_experiment(experiment_id)
    metric_map = _metric_by_video_id(metric)

    arms: dict[str, list[float]] = {}
    for v in videos:
        arm = v.get("experiment_arm")
        if not arm:
            continue
        val = metric_map.get(v["id"])
        if val is None:
            continue  # video chưa có số liệu — chưa đưa vào so sánh
        arms.setdefault(arm, []).append(val)

    arm_summary = {
        arm: {"n": len(vals), "mean": mean(vals) if vals else None, "values": vals}
        for arm, vals in arms.items()
    }
    result: dict = {
        "experiment_id": experiment_id,
        "metric": metric,
        "arms": arm_summary,
        "better": None,
        "delta": None,
        "p_value": None, "t": None, "df": None,
        # Cần ≥2 arm mới có gì để so — 1 arm dù nhiều mẫu vẫn KHÔNG "đủ mẫu"
        # (không có nhánh đối chứng), tránh format_comparison báo "đủ dữ liệu"
        # nhầm khi arm B chưa đăng.
        "enough_samples": len(arms) >= 2 and all(len(v) >= min_samples for v in arms.values()),
        "recommended_samples_met": len(arms) >= 2 and all(
            len(v) >= RECOMMENDED_MIN_SAMPLES for v in arms.values()),
    }

    if not arms:
        result["note"] = "Chưa có video nào có số liệu cho thí nghiệm này."
        return result

    means = {arm: mean(vals) for arm, vals in arms.items() if vals}
    result["better"] = (max(means, key=means.get)
                        if len(set(means.values())) > 1 else "tie")

    if len(arms) == 2:
        (arm_a, vals_a), (arm_b, vals_b) = sorted(arms.items())
        tt = welch_ttest(vals_a, vals_b)
        result.update({"t": tt["t"], "df": tt["df"], "p_value": tt["p_value"]})
        if result["better"] not in (None, "tie"):
            other = arm_b if result["better"] == arm_a else arm_a
            result["delta"] = means[result["better"]] - means[other]

    if not result["enough_samples"]:
        result["note"] = (
            f"Chưa đủ mẫu (cần ≥{min_samples}/arm; khuyến nghị "
            f"≥{RECOMMENDED_MIN_SAMPLES} trước khi quyết định).")
    elif not result["recommended_samples_met"]:
        result["note"] = (
            f"Đủ {min_samples}/arm để xem xu hướng, nhưng nên chờ "
            f"≥{RECOMMENDED_MIN_SAMPLES}/arm trước khi cắt/nhân format.")
    else:
        result["note"] = "Đủ mẫu khuyến nghị — có thể ra quyết định."
    return result


def format_comparison(result: dict) -> str:
    """Render kết quả compare_arms thành text ngắn (Telegram / CLI)."""
    lines = [f"🧪 {result['experiment_id']} — metric: {result['metric']}"]
    for arm, s in sorted(result["arms"].items()):
        m = f"{s['mean']:.1f}" if s["mean"] is not None else "—"
        lines.append(f"  Arm {arm}: n={s['n']}, mean={m}")
    if result.get("better") and result["better"] != "tie":
        delta = result.get("delta")
        d = f" (Δ {delta:+.1f})" if delta is not None else ""
        lines.append(f"  → Nhỉnh hơn: {result['better']}{d}")
    elif result.get("better") == "tie":
        lines.append("  → Hoà")
    if result.get("p_value") is not None:
        lines.append(f"  p-value: {result['p_value']:.3f}")
    lines.append(f"  {result.get('note', '')}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="So sánh A/B experiment (Phase 6)")
    parser.add_argument("experiment_id")
    parser.add_argument("--metric", default="views")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    args = parser.parse_args()
    print(format_comparison(compare_arms(args.experiment_id, args.metric, args.min_samples)))
