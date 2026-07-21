from __future__ import annotations

"""
Google Sheets Drama Importer — cầu nối nguồn ngoài cho track Drama.

Bối cảnh: Reddit khoá OAuth tự phục vụ (issue #78), Lemmy drama cạn (issue #90),
còn các nguồn "béo bở" khác (Reddit RSS, confession Facebook, truyện dịch) đều
KHÔNG có API ổn định để pipeline cào trực tiếp — Reddit chặn fetcher datacenter
từng đợt (403), Facebook không có RSS chính thức, Quora đã khai tử RSS. Thay vì
viết N collector mỏng manh cho N nguồn, module này đọc MỘT Google Sheet làm
phễu nạp chung:

    [Make.com RSS → Sheets]  ┐
    [RSS.app / Zapier]       ├──► Google Sheet ──► collect_all_gsheet() ──► stories
    [dán tay confession VN]  ┘

- Tầng cào (Make.com Free 1.000 ops/tháng, RSS module + Google Sheets "Add a
  Row") sống NGOÀI pipeline: khi Reddit chặn Make thì scenario nghỉ, sheet còn
  nguyên, pipeline không hỏng. Đổi/thêm nguồn = sửa scenario, không đổi code.
- Tầng nạp (module này) chỉ cần sheet share "Anyone with link – Viewer" (hoặc
  Publish to web → CSV): tải CSV export bằng stdlib urllib, auto-dò cột
  title/content/url/source (header Anh hoặc Việt), gỡ HTML (RSS content thường
  là HTML), dedupe qua `source_id` rồi insert vào `stories` — từ đó
  score → rewrite → render chạy như mọi nguồn khác.

Setup chi tiết (tạo sheet, dựng scenario Make.com): docs/current/gsheet-drama-source.md.

Chạy tay: python -m collectors.gsheet_drama_importer
Trong pipeline: bước collect của main_drama.py gọi collect_all_gsheet()
(best-effort như Reddit/Lemmy/HF — nguồn lỗi không kéo sập cả bước).
"""

import csv
import hashlib
import html
import io
import logging
import re
import urllib.error
import urllib.request

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.stories import insert_story, dedupe_check

logger = logging.getLogger(__name__)

SOURCE_NAME = "gsheet"

# Header candidates per logical column, matched case-insensitively after
# accent-stripping ("Tiêu đề" == "tieu de"). First hit wins, ordered by how
# specific the name is. Make.com's RSS module maps naturally onto
# Title/Content/URL; Vietnamese headers cover hand-maintained sheets.
_COLUMN_CANDIDATES = {
    "title": ("title", "tieu de", "headline", "subject", "ten bai"),
    "content": ("content", "body", "noi dung", "story", "selftext", "text",
                "description", "summary"),
    "url": ("url", "link", "duong dan", "lien ket"),
    "source": ("source", "nguon", "feed", "kenh"),
}

_TAG_RE = re.compile(r"<[^>]+>")


class GSheetFetchError(Exception):
    """Sheet unreachable/unusable (network, HTTP error, oversized, no CSV)."""


def _strip_accents(text: str) -> str:
    import unicodedata
    # NFD tách dấu thanh/dấu mũ thành combining mark rồi bỏ; riêng đ/Đ là chữ
    # cái độc lập (không decompose) nên map tay — thiếu nó "Tiêu đề" không bao
    # giờ khớp candidate "tieu de".
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def _export_csv_url(url: str) -> str:
    """Chuyển link Google Sheet bất kỳ về dạng CSV export tải được.

    Nhận cả 3 dạng người dùng hay dán:
    - Link edit thường:  .../spreadsheets/d/<ID>/edit#gid=<GID>
      → .../spreadsheets/d/<ID>/export?format=csv&gid=<GID> (share "Anyone
      with link" là tải được, không cần Publish to web).
    - Link đã Publish to web (…/pub?output=csv, /export?format=csv): giữ nguyên.
    - URL CSV trực tiếp bất kỳ (không phải Google): giữ nguyên — cầu nối này
      dùng được cho mọi nơi xuất CSV, không khoá vào Google.
    """
    m = re.search(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m or "output=csv" in url or "format=csv" in url:
        return url
    sheet_id = m.group(1)
    gid_match = re.search(r"[#?&]gid=(\d+)", url)
    gid = gid_match.group(1) if gid_match else "0"
    return (f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
            f"?format=csv&gid={gid}")


def _fetch_csv_text(url: str) -> str:
    """Tải CSV (stdlib urllib, UA theo issue #97, cap dung lượng chống hostile)."""
    request = urllib.request.Request(
        _export_csv_url(url), headers={"User-Agent": config.HTTP_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=config.GSHEET_TIMEOUT) as resp:
            data = resp.read(config.GSHEET_MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise GSheetFetchError(f"cannot fetch sheet: {e}") from e
    if len(data) > config.GSHEET_MAX_BYTES:
        raise GSheetFetchError(
            f"sheet exceeds GSHEET_MAX_BYTES={config.GSHEET_MAX_BYTES} — "
            "refusing oversized download")
    # utf-8-sig: Google/Excel exports hay kèm BOM; lỗi decode thay bằng U+FFFD
    # thay vì chết cả batch vì 1 byte hỏng.
    text = data.decode("utf-8-sig", errors="replace")
    # Trang HTML (login/permission) thay vì CSV = sheet chưa share công khai.
    if text.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        raise GSheetFetchError(
            "got an HTML page instead of CSV — sheet chưa share "
            "'Anyone with the link – Viewer' (hoặc chưa Publish to web dạng CSV)")
    return text


def _resolve_columns(header: list[str]) -> dict[str, int]:
    """Map cột logic → index từ hàng header. Cần tối thiểu title + content."""
    normalized = [_strip_accents(h or "").strip().lower() for h in header]
    resolved: dict[str, int] = {}
    for logical, candidates in _COLUMN_CANDIDATES.items():
        for cand in candidates:
            if cand in normalized:
                resolved[logical] = normalized.index(cand)
                break
    missing = [c for c in ("title", "content") if c not in resolved]
    if missing:
        raise GSheetFetchError(
            f"sheet header {header!r} thiếu cột {missing} — đặt tên cột là "
            "Title/Content (hoặc 'Tiêu đề'/'Nội dung') ở hàng đầu tiên")
    return resolved


def _clean_html(text: str) -> str:
    """RSS content (nhất là Reddit) là HTML — gỡ tag/entity về văn bản thuần."""
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _row_source_id(url: str, title: str, content: str) -> str:
    """Dedupe key. Ưu tiên URL (ổn định khi Make nạp lại cùng bài); không có
    URL (dán tay) thì hash title+content — dán lại y nguyên sẽ bị bỏ trùng."""
    basis = url.strip() if url and url.strip() else f"{title}\n{content}"
    return f"{SOURCE_NAME}:{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:32]}"


def collect_all_gsheet() -> int:
    """Nạp story mới từ Google Sheet đã cấu hình. Trả về số story insert.

    - GSHEET_DRAMA_URL rỗng → nguồn tắt, trả 0 (như REDDIT_ENABLED=0).
    - Quét TOÀN BỘ hàng để dedupe, nhưng chỉ insert tối đa GSHEET_IMPORT_LIMIT
      story mới mỗi lần chạy — một cú dán 300 dòng không nã hết ngân sách Haiku
      của ngày; phần còn lại tự vào ở các lần chạy sau.
    - Hàng thiếu title, content quá ngắn (< GSHEET_MIN_BODY_CHARS sau khi gỡ
      HTML — item RSS chỉ-có-link, hàng rác) bị bỏ qua, có đếm log.
    """
    if not config.GSHEET_DRAMA_URL:
        logger.info("Google Sheet drama source disabled (GSHEET_DRAMA_URL empty)")
        return 0

    text = _fetch_csv_text(config.GSHEET_DRAMA_URL)
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        logger.warning("Google Sheet is empty")
        return 0

    columns = _resolve_columns(rows[0])
    inserted = skipped_dupe = skipped_thin = 0

    for row in rows[1:]:
        if inserted >= config.GSHEET_IMPORT_LIMIT:
            logger.info("GSHEET_IMPORT_LIMIT=%d reached — remaining rows will "
                        "import on later runs", config.GSHEET_IMPORT_LIMIT)
            break

        def _cell(logical: str) -> str:
            idx = columns.get(logical)
            return row[idx].strip() if idx is not None and idx < len(row) else ""

        title = _cell("title")
        content = _clean_html(_cell("content"))
        url = _cell("url")
        if not title or len(content) < config.GSHEET_MIN_BODY_CHARS:
            skipped_thin += 1
            continue

        source_id = _row_source_id(url, title, content)
        if dedupe_check(source_id):
            skipped_dupe += 1
            continue

        feed = _cell("source")
        metadata = {"origin": "gsheet"}
        if url:
            metadata["url"] = url
        if feed:
            metadata["feed"] = feed
        insert_story(
            source=SOURCE_NAME,
            source_id=source_id,
            raw_content=f"{title}\n\n{content}",
            track="drama",
            title=title,
            metadata=metadata,
        )
        inserted += 1

    logger.info(
        "Google Sheet import: %d new, %d duplicate, %d thin/skipped (rows=%d)",
        inserted, skipped_dupe, skipped_thin, len(rows) - 1,
    )
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    from storage.database import init_db
    init_db()
    print(f"Imported {collect_all_gsheet()} stories from Google Sheet")
