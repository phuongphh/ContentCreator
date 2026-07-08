from __future__ import annotations

"""
Tầng dữ liệu cho dashboard (Phase 6) — pure DB, KHÔNG import streamlit nên
unit-test được. app.py chỉ render các dict/list mà module này trả về.
"""

import logging
from datetime import date, timedelta
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from channels import channels_for_platform, get_channel
from storage import video_metrics, channel_metrics, cost_logs
from storage.database import get_connection, get_video
from analytics import pricing

logger = logging.getLogger(__name__)

_KPI_METRICS = ("views", "likes", "comments", "shares")


def default_since(days: int = 30) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def overview(since: Optional[str] = None, platform: Optional[str] = None) -> dict:
    """KPI tổng: cộng metric snapshot MỚI NHẤT của mỗi video (tránh cộng trùng
    nhiều snapshot cùng 1 video). Returns {n_videos, views, likes, ...,
    avg_retention_50}."""
    rows = video_metrics.latest_per_video(platform=platform, since=since)
    totals = {m: 0 for m in _KPI_METRICS}
    retentions = []
    for r in rows:
        for m in _KPI_METRICS:
            if r.get(m) is not None:
                totals[m] += r[m]
        if r.get("retention_50_pct") is not None:
            retentions.append(r["retention_50_pct"])
    totals["n_videos"] = len(rows)
    totals["avg_retention_50"] = round(sum(retentions) / len(retentions), 1) if retentions else None
    return totals


def _label_for(row: dict) -> str:
    vid = row.get("video_id")
    if vid:
        v = get_video(vid)
        if v:
            title = (v.get("youtube_title") or v.get("tiktok_caption")
                     or v.get("script_text", "")[:40])
            if title:
                return title[:50]
    return f"{row.get('platform', '?')}:{row.get('external_id', '?')}"


def top_videos_table(metric: str = "views", limit: int = 10,
                     since: Optional[str] = None, ascending: bool = False) -> list[dict]:
    """Bảng top/bottom video: [{label, platform, metric_value, views, retention_50_pct}]."""
    rows = video_metrics.top_videos(metric=metric, limit=limit, since=since,
                                    ascending=ascending)
    return [
        {
            "label": _label_for(r),
            "platform": r.get("platform"),
            metric: r.get(metric),
            "views": r.get("views"),
            "retention_50_pct": r.get("retention_50_pct"),
        }
        for r in rows
    ]


def views_timeseries(since: Optional[str] = None,
                     platform: Optional[str] = None) -> dict[str, int]:
    """{snapshot_date: tổng views} — cho line chart 30 ngày."""
    where = []
    params: list = []
    if since:
        where.append("snapshot_date >= ?")
        params.append(since)
    if platform:
        where.append("platform = ?")
        params.append(platform)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT snapshot_date, COALESCE(SUM(views), 0) AS v FROM video_metrics "
            f"{where_sql} GROUP BY snapshot_date ORDER BY snapshot_date",
            params,
        ).fetchall()
        return {r["snapshot_date"]: int(r["v"]) for r in rows}
    finally:
        conn.close()


def format_breakdown(since: Optional[str] = None) -> list[dict]:
    """Retention/views trung bình theo (track, video_type) — 'format' của video.

    Join snapshot mới nhất với bảng videos để biết track/loại. Video chưa map
    (video_id NULL) gộp vào nhóm 'unmapped'.
    """
    latest = video_metrics.latest_per_video(since=since)
    groups: dict[tuple, dict] = {}
    for r in latest:
        vid = r.get("video_id")
        track = video_type = None
        if vid:
            v = get_video(vid)
            if v:
                track = v.get("track")
                video_type = v.get("video_type")
        key = (track or "unmapped", video_type or "?")
        g = groups.setdefault(key, {"track": key[0], "video_type": key[1],
                                    "n": 0, "views": 0, "_ret": []})
        g["n"] += 1
        if r.get("views") is not None:
            g["views"] += r["views"]
        if r.get("retention_50_pct") is not None:
            g["_ret"].append(r["retention_50_pct"])
    out = []
    for g in groups.values():
        rets = g.pop("_ret")
        g["avg_retention_50"] = round(sum(rets) / len(rets), 1) if rets else None
        out.append(g)
    out.sort(key=lambda g: g["views"], reverse=True)
    return out


def sub_growth(since: Optional[str] = None) -> list[dict]:
    """[{channel_key, name, subs_gained, subscribers}] cho mỗi kênh YouTube."""
    since = since or default_since()
    out = []
    for channel_key in channels_for_platform("youtube"):
        rng = channel_metrics.get_range(channel_key, since=since)
        latest_subs = rng[-1]["subscribers"] if rng else None
        out.append({
            "channel_key": channel_key,
            "name": get_channel(channel_key)["name"],
            "subs_gained": channel_metrics.subs_gained(channel_key, since),
            "subscribers": latest_subs,
        })
    return out


def cost_breakdown(since: Optional[str] = None) -> dict:
    """{'summary': summarize_costs(...), 'daily': [{date, service, ...}]}."""
    since = since or default_since()
    return {
        "summary": pricing.summarize_costs(cost_logs.rows_since(since)),
        "daily": cost_logs.daily_totals(since=since),
    }
