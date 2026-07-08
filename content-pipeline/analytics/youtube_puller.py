from __future__ import annotations

"""
YouTube Analytics puller (Phase 6 EPIC #6.1) — kéo số liệu per-video + per-channel
qua YouTube Analytics API v2, upsert vào `video_metrics` / `channel_metrics`.

Chạy mỗi đêm 23h (launchd com.ai5phut.metrics-pull.plist). Metric YouTube trễ
24-48h (phase-6-detailed.md §5) nên snapshot mỗi ngày ghi đè trong ngày là bình
thường — upsert idempotent lo việc đó.

## Scope OAuth
Analytics cần `yt-analytics.readonly` + `youtube.readonly` — KHÁC token upload
(`youtube.upload` + `force-ssl`, publisher/youtube_uploader.py). Nên puller
dùng file token RIÊNG cho mỗi kênh, suy ra từ token upload:
    <upload_token>.analytics.json
Một OAuth2 client (Google Cloud) mint được nhiều token với scope khác nhau.
Chạy `python -m analytics.youtube_puller auth <channel_key>` một lần để cấp
token analytics cho từng kênh (mở browser, giống youtube_uploader.py __main__).

## Mapping video → videos.id
Analytics trả về YouTube video id; nối về `videos.id` qua
scheduled_posts.platform_video_id (storage.video_metrics.resolve_video_id).
Không map được (vd video đăng tay) → vẫn lưu metric với video_id=NULL.
"""

import logging
import os
from datetime import date, timedelta
from typing import Optional

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from channels import get_channel, channels_for_platform
from storage import video_metrics, channel_metrics

logger = logging.getLogger(__name__)

# Scope chỉ-đọc cho Analytics + Data API (khác scope upload).
SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# Metric per-video hợp lệ với dimension=video ở Analytics API v2. CTR
# (impressions) KHÔNG lấy được ổn định qua endpoint này nên để NULL — có thể
# bổ sung sau bằng report "impressions" riêng.
_VIDEO_METRICS_QUERY = (
    "views,likes,comments,shares,estimatedMinutesWatched,averageViewDuration"
)


def _analytics_token_file(channel_key: str) -> str:
    """File token analytics riêng cho kênh (suy ra từ token upload)."""
    from publisher.youtube_uploader import resolve_token_file
    upload_token = resolve_token_file(channel_key)
    return upload_token + ".analytics.json"


def _load_credentials(token_file: str, interactive: bool = False):
    """Load OAuth creds với SCOPES analytics; refresh nếu hết hạn.

    `interactive=True` (chỉ dùng ở CLI auth) sẽ mở browser cấp quyền lần đầu.
    Non-interactive mà token thiếu/không đủ scope → trả None kèm hướng dẫn.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        granted = set(getattr(creds, "scopes", None) or [])
        if not set(SCOPES).issubset(granted):
            logger.warning("Token %s thiếu scope analytics — cần auth lại.", token_file)
            creds = None

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        return creds

    if not interactive:
        logger.error(
            "Chưa có token analytics hợp lệ (%s). Chạy: "
            "python -m analytics.youtube_puller auth <channel_key>", token_file,
        )
        return None

    from google_auth_oauthlib.flow import InstalledAppFlow
    if not config.YOUTUBE_CLIENT_SECRETS or not os.path.exists(config.YOUTUBE_CLIENT_SECRETS):
        logger.error("YOUTUBE_CLIENT_SECRETS không tồn tại: %s", config.YOUTUBE_CLIENT_SECRETS)
        return None
    flow = InstalledAppFlow.from_client_secrets_file(config.YOUTUBE_CLIENT_SECRETS, SCOPES)
    creds = flow.run_local_server(port=0)
    os.makedirs(os.path.dirname(token_file) or ".", exist_ok=True)
    with open(token_file, "w") as f:
        f.write(creds.to_json())
    return creds


def _build_services(channel_key: str, interactive: bool = False):
    """(analytics_service, data_service) đã xác thực cho 1 kênh, hoặc (None, None)."""
    creds = _load_credentials(_analytics_token_file(channel_key), interactive=interactive)
    if not creds:
        return None, None
    from googleapiclient.discovery import build
    analytics = build("youtubeAnalytics", "v2", credentials=creds)
    data = build("youtube", "v3", credentials=creds)
    return analytics, data


def _date_range(days_back: int) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=max(1, days_back))
    return start.isoformat(), end.isoformat()


def _rows_to_records(response: dict) -> list[dict]:
    """Chuyển response Analytics (columnHeaders + rows) thành list dict theo tên cột."""
    headers = [h["name"] for h in response.get("columnHeaders", [])]
    records = []
    for row in response.get("rows", []) or []:
        records.append(dict(zip(headers, row)))
    return records


def _retention_50(analytics, external_id: str, start: str, end: str) -> Optional[float]:
    """% khán giả còn xem ở mốc 50% video (report audienceRetention), best-effort.

    Đây là metric chiến lược ưu tiên (phase-6-detailed.md §5: đừng chỉ nhìn
    views). Report riêng theo từng video; video mới/không đủ dữ liệu → trả None
    chứ không làm hỏng cả lần pull.
    """
    try:
        resp = analytics.reports().query(
            ids="channel==MINE",
            startDate=start, endDate=end,
            metrics="audienceWatchRatio",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={external_id};audienceType==ORGANIC",
        ).execute()
    except Exception as e:
        logger.debug("retention_50 unavailable for %s: %s", external_id, e)
        return None
    records = _rows_to_records(resp)
    if not records:
        return None
    # Lấy điểm có elapsedVideoTimeRatio gần 0.5 nhất.
    best = min(records, key=lambda r: abs((r.get("elapsedVideoTimeRatio") or 0) - 0.5))
    ratio = best.get("audienceWatchRatio")
    return round(ratio * 100, 2) if ratio is not None else None


def pull_metrics_for_channel(channel_key: str, days_back: int = 7,
                             with_retention: bool = True,
                             analytics=None, data=None,
                             snapshot_date: Optional[str] = None) -> int:
    """Kéo + upsert số liệu 1 kênh YouTube. Returns số video snapshot được ghi.

    `analytics`/`data`: inject service (cho test); mặc định tự xác thực.
    """
    channel = get_channel(channel_key)
    if channel["platform"] != "youtube":
        logger.error("%s không phải kênh YouTube", channel_key)
        return 0

    if analytics is None or data is None:
        analytics, data = _build_services(channel_key)
        if not analytics:
            return 0

    start, end = _date_range(days_back)

    # 1. Per-video.
    try:
        resp = analytics.reports().query(
            ids="channel==MINE", startDate=start, endDate=end,
            metrics=_VIDEO_METRICS_QUERY, dimensions="video",
            sort="-views", maxResults=200,
        ).execute()
    except Exception as e:
        logger.error("pull per-video failed for %s: %s", channel_key, e)
        return 0

    count = 0
    for rec in _rows_to_records(resp):
        external_id = rec.get("video")
        if not external_id:
            continue
        retention = (_retention_50(analytics, external_id, start, end)
                     if with_retention else None)
        video_metrics.upsert_metric(
            platform="youtube", external_id=external_id, channel_key=channel_key,
            snapshot_date=snapshot_date,
            views=_int(rec.get("views")), likes=_int(rec.get("likes")),
            comments=_int(rec.get("comments")), shares=_int(rec.get("shares")),
            watch_time_minutes=_float(rec.get("estimatedMinutesWatched")),
            avg_view_duration_seconds=_float(rec.get("averageViewDuration")),
            retention_50_pct=retention,
        )
        count += 1

    # 2. Per-channel (sub growth + view cho weekly retro).
    _pull_channel_totals(channel_key, start, end, analytics, data, snapshot_date)

    logger.info("Pulled %d video snapshots for %s", count, channel_key)
    return count


def _pull_channel_totals(channel_key: str, start: str, end: str,
                         analytics, data, snapshot_date: Optional[str]) -> None:
    subs_gained = channel_views = None
    try:
        resp = analytics.reports().query(
            ids="channel==MINE", startDate=start, endDate=end,
            metrics="subscribersGained,subscribersLost,views",
        ).execute()
        recs = _rows_to_records(resp)
        if recs:
            r = recs[0]
            gained = _int(r.get("subscribersGained")) or 0
            lost = _int(r.get("subscribersLost")) or 0
            subs_gained = gained - lost
            channel_views = _int(r.get("views"))
    except Exception as e:
        logger.debug("channel totals unavailable for %s: %s", channel_key, e)

    # Tổng subscriber tuyệt đối (Data API statistics).
    subscribers = None
    try:
        ch = data.channels().list(part="statistics", mine=True).execute()
        items = ch.get("items", [])
        if items:
            subscribers = _int(items[0]["statistics"].get("subscriberCount"))
    except Exception as e:
        logger.debug("subscriberCount unavailable for %s: %s", channel_key, e)

    if any(v is not None for v in (subs_gained, channel_views, subscribers)):
        channel_metrics.upsert_channel_metric(
            channel_key=channel_key, platform="youtube",
            snapshot_date=snapshot_date, subscribers=subscribers,
            subscribers_gained=subs_gained, views=channel_views,
        )


def pull_all(days_back: int = 7) -> dict[str, int]:
    """Pull mọi kênh YouTube trong registry. Returns {channel_key: n_videos}."""
    result = {}
    for channel_key in channels_for_platform("youtube"):
        try:
            result[channel_key] = pull_metrics_for_channel(channel_key, days_back)
        except Exception as e:
            logger.error("pull_all: %s failed: %s", channel_key, e)
            result[channel_key] = 0
    return result


def _int(v):
    try:
        return int(round(float(v))) if v is not None else None
    except (ValueError, TypeError):
        return None


def _float(v):
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="YouTube Analytics puller (Phase 6)")
    sub = parser.add_subparsers(dest="command")

    p_auth = sub.add_parser("auth", help="Cấp token analytics cho 1 kênh (mở browser)")
    p_auth.add_argument("channel_key")

    p_pull = sub.add_parser("pull", help="Pull số liệu")
    p_pull.add_argument("channel_key", nargs="?", help="Bỏ trống = mọi kênh YouTube")
    p_pull.add_argument("--days-back", type=int, default=7)

    args = parser.parse_args()
    if args.command == "auth":
        creds = _load_credentials(_analytics_token_file(args.channel_key), interactive=True)
        print("✅ Token analytics đã lưu." if creds else "❌ Auth thất bại.")
    elif args.command == "pull" and args.channel_key:
        print(pull_metrics_for_channel(args.channel_key, days_back=args.days_back), "video")
    else:
        print(pull_all(days_back=getattr(args, "days_back", 7)))
