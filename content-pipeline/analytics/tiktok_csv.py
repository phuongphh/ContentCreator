from __future__ import annotations

"""
TikTok CSV parser (Phase 6 EPIC #6.1, giai đoạn 1 — trước khi có TikTok API).

TikTok Studio cho export CSV số liệu video. Phuong tải file, gửi qua Telegram
(lệnh /import_tiktok_csv → đính kèm file), bot parse vào `video_metrics`
(platform='tiktok'). Giai đoạn 2 (Display/Insights API) để sau khi app TikTok
được duyệt (task external từ Phase 5).

Parser cố ý KHOAN DUNG với header: TikTok Studio đổi tên cột giữa các phiên bản
/ ngôn ngữ (Anh/Việt), và số có thể ở dạng "1.2K", "1,234", "45%". Cột không
nhận diện được → bỏ qua (không làm hỏng cả file). Không map được external_id →
skip dòng đó và báo trong summary.
"""

import csv
import io
import logging
import re
from datetime import datetime
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage import video_metrics

logger = logging.getLogger(__name__)

# Alias header (đã chuẩn hoá lowercase, bỏ khoảng trắng thừa) → field nội bộ.
_HEADER_ALIASES: dict[str, str] = {
    # views
    "views": "views", "video views": "views", "total views": "views",
    "play count": "views", "lượt xem": "views", "số lượt xem": "views",
    # likes
    "likes": "likes", "total likes": "likes", "lượt thích": "likes",
    # comments
    "comments": "comments", "total comments": "comments", "bình luận": "comments",
    "lượt bình luận": "comments",
    # shares
    "shares": "shares", "total shares": "shares", "lượt chia sẻ": "shares",
    # avg watch time (seconds)
    "average watch time": "avg_view_duration_seconds",
    "avg watch time": "avg_view_duration_seconds",
    "thời gian xem trung bình": "avg_view_duration_seconds",
    # retention proxy: % xem hết video
    "watched full video": "retention_50_pct",
    "completion rate": "retention_50_pct",
    "tỷ lệ xem hết": "retention_50_pct",
}

_ID_HEADERS = {"video id", "id", "video link", "video url", "link", "url",
               "liên kết video", "đường dẫn"}
_TITLE_HEADERS = {"video title", "title", "tiêu đề", "tên video"}
_DATE_HEADERS = {"post time", "date", "posted", "ngày đăng", "thời gian đăng"}

_TIKTOK_ID_RE = re.compile(r"/video/(\d+)")
_TRAILING_ID_RE = re.compile(r"(\d{6,})")


def _norm_header(h: str) -> str:
    return (h or "").strip().lower().lstrip("﻿")


def _num(value) -> Optional[float]:
    """Parse '1.2K', '1,234', '45%', '12' → float. None nếu không parse được."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    mult = 1.0
    if s[-1:].upper() == "K":
        mult, s = 1_000.0, s[:-1]
    elif s[-1:].upper() == "M":
        mult, s = 1_000_000.0, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _duration_seconds(value) -> Optional[float]:
    """Parse thời lượng: '0:12' / '1:05' (mm:ss) hoặc '12' / '12.3s' → giây."""
    if value is None:
        return None
    s = str(value).strip().lower().rstrip("s").strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        secs = 0.0
        for n in nums:
            secs = secs * 60 + n
        return secs
    return _num(s)


def _extract_external_id(row: dict) -> Optional[str]:
    """Rút external_id ổn định từ 1 dòng: ưu tiên id số trong link TikTok."""
    for key, val in row.items():
        if _norm_header(key) in _ID_HEADERS and val:
            v = str(val).strip()
            m = _TIKTOK_ID_RE.search(v) or _TRAILING_ID_RE.search(v)
            if m:
                return m.group(1)
            if v:
                return v  # dùng nguyên link/id nếu không tách được số
    # Fallback: title (không lý tưởng nhưng còn hơn bỏ dòng).
    for key, val in row.items():
        if _norm_header(key) in _TITLE_HEADERS and val:
            return str(val).strip()[:80]
    return None


def _extract_date(row: dict) -> Optional[str]:
    for key, val in row.items():
        if _norm_header(key) in _DATE_HEADERS and val:
            raw = str(val).strip()
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(raw[:len(fmt) + 4].strip(), fmt).date().isoformat()
                except ValueError:
                    continue
    return None


def parse_csv_text(text: str) -> list[dict]:
    """Parse CSV → list record chuẩn hoá {external_id, snapshot_date?, <metrics>}.

    Dòng không rút được external_id bị bỏ (kèm cảnh báo debug).
    """
    reader = csv.DictReader(io.StringIO(text))
    records = []
    for raw_row in reader:
        external_id = _extract_external_id(raw_row)
        if not external_id:
            logger.debug("Bỏ 1 dòng CSV không có video id/link: %s", raw_row)
            continue
        record: dict = {"external_id": external_id}
        d = _extract_date(raw_row)
        if d:
            record["snapshot_date"] = d
        for key, val in raw_row.items():
            field = _HEADER_ALIASES.get(_norm_header(key))
            if not field:
                continue
            if field == "avg_view_duration_seconds":
                parsed = _duration_seconds(val)
            else:
                parsed = _num(val)
            if parsed is not None:
                # views/likes/... là số nguyên; retention/duration để float.
                if field in ("views", "likes", "comments", "shares"):
                    record[field] = int(round(parsed))
                else:
                    record[field] = parsed
        records.append(record)
    return records


def import_csv_text(text: str, snapshot_date: Optional[str] = None) -> dict:
    """Parse + upsert vào video_metrics. Returns summary.

    `snapshot_date`: ép ngày snapshot cho mọi dòng (nếu CSV không có cột ngày,
    hoặc muốn gộp cả file vào 1 ngày). Ưu tiên hơn ngày rút từ từng dòng.
    """
    records = parse_csv_text(text)
    imported = 0
    metric_fields = {"views", "likes", "comments", "shares",
                     "avg_view_duration_seconds", "retention_50_pct"}
    for rec in records:
        metrics = {k: v for k, v in rec.items() if k in metric_fields}
        if not metrics:
            continue  # dòng chỉ có id, không có số liệu — bỏ
        video_metrics.upsert_metric(
            platform="tiktok", external_id=rec["external_id"],
            snapshot_date=snapshot_date or rec.get("snapshot_date"),
            **metrics,
        )
        imported += 1
    summary = {"rows": len(records), "imported": imported,
               "skipped": len(records) - imported}
    logger.info("TikTok CSV import: %s", summary)
    return summary


def import_csv_file(path: str, snapshot_date: Optional[str] = None) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return import_csv_text(f.read(), snapshot_date=snapshot_date)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Import TikTok Studio CSV (Phase 6)")
    parser.add_argument("csv_path")
    parser.add_argument("--date", help="Ép snapshot_date YYYY-MM-DD cho mọi dòng")
    args = parser.parse_args()
    print(import_csv_file(args.csv_path, snapshot_date=args.date))
