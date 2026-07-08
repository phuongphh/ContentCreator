from __future__ import annotations

"""
KPI dashboard Streamlit (Phase 6 EPIC #6.2).

Chạy local trên Mac Mini:  streamlit run dashboard/app.py
(streamlit là optional dependency — xem requirements.txt; cài: pip install streamlit)

Toàn bộ logic dữ liệu nằm ở dashboard/data.py (test được không cần streamlit);
file này chỉ render. 4 tab: Overview, Top videos, Format analysis, Cost.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import streamlit as st
except ImportError:  # pragma: no cover - chỉ chạy khi thiếu streamlit
    sys.stderr.write(
        "streamlit chưa được cài. Chạy: pip install streamlit\n"
        "rồi: streamlit run dashboard/app.py\n"
    )
    sys.exit(1)

from channels import channels_for_platform
from dashboard import data


def _sidebar():
    st.sidebar.title("📊 AI 5 Phút — KPI")
    days = st.sidebar.selectbox("Khoảng thời gian", [7, 14, 30, 90], index=2,
                                format_func=lambda d: f"{d} ngày gần nhất")
    platforms = ["Tất cả", "youtube", "tiktok"]
    platform = st.sidebar.selectbox("Platform", platforms)
    return {
        "since": data.default_since(days),
        "platform": None if platform == "Tất cả" else platform,
    }


def _tab_overview(cfg):
    ov = data.overview(since=cfg["since"], platform=cfg["platform"])
    cols = st.columns(4)
    cols[0].metric("Video có số liệu", ov["n_videos"])
    cols[1].metric("Tổng views", f"{ov['views']:,}")
    cols[2].metric("Tổng likes", f"{ov['likes']:,}")
    cols[3].metric("Retention 50% TB",
                   f"{ov['avg_retention_50']}%" if ov["avg_retention_50"] is not None else "—")

    st.subheader("Views theo ngày")
    ts = data.views_timeseries(since=cfg["since"], platform=cfg["platform"])
    if ts:
        st.line_chart({"views": list(ts.values())}, x=None)
        st.caption("Trục X: " + ", ".join(ts.keys()))
    else:
        st.info("Chưa có snapshot nào trong khoảng này.")

    st.subheader("Sub growth từng kênh")
    st.table(data.sub_growth(since=cfg["since"]))


def _tab_top_videos(cfg):
    metric = st.selectbox("Xếp theo", ["views", "retention_50_pct",
                                       "avg_view_duration_seconds", "likes"])
    st.subheader(f"Top 10 — {metric}")
    st.table(data.top_videos_table(metric=metric, limit=10, since=cfg["since"]))
    st.subheader("Bottom 5 (cần phân tích)")
    st.table(data.top_videos_table(metric="retention_50_pct", limit=5,
                                   since=cfg["since"], ascending=True))


def _tab_format(cfg):
    st.subheader("Retention / views theo format")
    rows = data.format_breakdown(since=cfg["since"])
    if rows:
        st.table(rows)
    else:
        st.info("Chưa có dữ liệu format.")


def _tab_cost(cfg):
    cb = data.cost_breakdown(since=cfg["since"])
    summary = cb["summary"]
    st.metric("Tổng chi phí AI (USD)", f"${summary['total_usd']:.2f}")
    if summary["unpriced_models"]:
        st.warning("Model chưa có giá: " + ", ".join(summary["unpriced_models"]))
    st.subheader("Theo model")
    st.table([
        {"model": m, **v} for m, v in summary["by_model"].items()
    ])
    st.subheader("Theo ngày")
    st.table(cb["daily"])


def main():
    st.set_page_config(page_title="AI 5 Phút — KPI", layout="wide")
    cfg = _sidebar()
    st.title("KPI Dashboard")
    tabs = st.tabs(["Overview", "Top videos", "Format analysis", "Cost"])
    with tabs[0]:
        _tab_overview(cfg)
    with tabs[1]:
        _tab_top_videos(cfg)
    with tabs[2]:
        _tab_format(cfg)
    with tabs[3]:
        _tab_cost(cfg)


if __name__ == "__main__":
    main()
