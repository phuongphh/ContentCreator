from __future__ import annotations

"""
Weekly retro (Phase 6 EPIC #6.4) — báo cáo tuần tự động qua Telegram.

Cron Chủ nhật 19h (launchd com.ai5phut.weekly-retro.plist): Phuong đọc tối CN,
sáng thứ 2 đã có quyết định. 5 mục (phase-6-detailed.md §3.6): top 3, bottom 3,
sub growth từng kênh, chi phí tuần, action items. Gói gọn ≤1500 ký tự để vừa 1
message Telegram + kèm link deep-dive tới dashboard.

`generate_retro_report()` là pure (đọc DB, trả str) nên unit-test được;
`send_weekly_retro()` mới gọi Telegram.
"""

import logging
from datetime import date, timedelta
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from channels import channels_for_platform, get_channel
from storage import video_metrics, channel_metrics, cost_logs
from storage.database import get_video
from analytics import pricing

logger = logging.getLogger(__name__)

MAX_REPORT_CHARS = 1500


def _week_ago(now: Optional[date] = None) -> str:
    now = now or date.today()
    return (now - timedelta(days=7)).isoformat()


def _label_for(row: dict) -> str:
    """Nhãn ngắn cho 1 metric row: tên video nếu map được, không thì external_id."""
    vid = row.get("video_id")
    if vid:
        v = get_video(vid)
        if v:
            title = (v.get("youtube_title") or v.get("tiktok_caption")
                     or v.get("script_text", "")[:40])
            if title:
                return title[:45]
    return f"{row.get('platform', '?')}:{row.get('external_id', '?')}"


def _fmt_int(v) -> str:
    return f"{int(v):,}" if v is not None else "—"


def generate_retro_report(since: Optional[str] = None,
                          now: Optional[date] = None) -> str:
    """Sinh báo cáo tuần (str ≤ MAX_REPORT_CHARS)."""
    since = since or _week_ago(now)
    today = (now or date.today()).isoformat()
    lines = [f"🗓️ RETRO TUẦN — {today}", ""]

    # 1. Top 3 theo views.
    top = video_metrics.top_videos(metric="views", limit=3, since=since)
    if top:
        lines.append("🔥 TOP 3 (views):")
        for r in top:
            ret = r.get("retention_50_pct")
            ret_s = f", ret50 {ret:.0f}%" if ret is not None else ""
            lines.append(f"  • {_label_for(r)} — {_fmt_int(r.get('views'))} view{ret_s}")
    else:
        lines.append("🔥 TOP 3: chưa có số liệu tuần này.")

    # 2. Bottom 3 theo retention (video cần phân tích) — chỉ xét video có retention.
    bottom = video_metrics.top_videos(metric="retention_50_pct", limit=3,
                                      since=since, ascending=True)
    if bottom:
        lines.append("")
        lines.append("🧊 CẦN XEM LẠI (retention thấp):")
        for r in bottom:
            lines.append(
                f"  • {_label_for(r)} — ret50 {r.get('retention_50_pct'):.0f}%, "
                f"{_fmt_int(r.get('views'))} view")

    # 3. Sub growth từng kênh YouTube.
    lines.append("")
    lines.append("📈 SUB GROWTH (7 ngày):")
    for channel_key in channels_for_platform("youtube"):
        gained = channel_metrics.subs_gained(channel_key, since)
        name = get_channel(channel_key)["name"]
        sign = "+" if gained >= 0 else ""
        lines.append(f"  • {name}: {sign}{gained} sub")

    # 4. Chi phí tuần.
    summary = pricing.summarize_costs(cost_logs.rows_since(since))
    lines.append("")
    lines.append(f"💸 CHI PHÍ AI: ${summary['total_usd']:.2f}")
    if summary["unpriced_models"]:
        lines.append(f"  (chưa có giá: {', '.join(summary['unpriced_models'])})")

    # 5. Action items — gợi ý dựa trên dữ liệu, không phán bằng cảm tính.
    lines.append("")
    lines.append("🎯 ĐỀ XUẤT:")
    for item in _action_items(top, bottom, summary):
        lines.append(f"  • {item}")

    lines.append("")
    lines.append("🔍 Chi tiết: streamlit run dashboard/app.py")

    report = "\n".join(lines)
    if len(report) > MAX_REPORT_CHARS:
        report = report[:MAX_REPORT_CHARS - 20].rstrip() + "\n… (xem dashboard)"
    return report


def _action_items(top: list[dict], bottom: list[dict], cost_summary: dict) -> list[str]:
    """Gợi ý hành động — có dữ liệu thì cụ thể, thiếu thì nhắc thu thập thêm.

    Cố ý KHÔNG đề xuất cắt format ở đây (quy tắc ≥10 sample/arm — dùng
    analytics/experiment_compare.py cho quyết định đó); retro chỉ nêu tín hiệu.
    """
    items: list[str] = []
    if top:
        best = top[0]
        items.append(f"Nhân motif của '{_label_for(best)}' (top view tuần).")
    if bottom and bottom[0].get("retention_50_pct") is not None:
        worst = bottom[0]
        if worst["retention_50_pct"] < 40:
            items.append(
                f"Xem lại hook của '{_label_for(worst)}' (retention "
                f"{worst['retention_50_pct']:.0f}% < 40%).")
    if cost_summary["total_usd"] > 0:
        items.append("Đối chiếu chi phí với hoá đơn Anthropic cuối tháng.")
    if not items:
        items.append("Chưa đủ dữ liệu — tiếp tục thu thập, chưa quyết định cắt format.")
    return items


def send_weekly_retro(since: Optional[str] = None) -> bool:
    """Sinh + gửi báo cáo tuần qua Telegram. Returns True nếu gửi thành công."""
    report = generate_retro_report(since=since)
    try:
        from notifier.telegram_bot import send_alert
        return send_alert(report)
    except Exception as e:
        logger.error("send_weekly_retro failed: %s", e)
        return False


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Weekly retro report (Phase 6)")
    parser.add_argument("--print", action="store_true",
                        help="In ra stdout thay vì gửi Telegram")
    args = parser.parse_args()
    if args.print:
        print(generate_retro_report())
    else:
        print("sent" if send_weekly_retro() else "failed")
