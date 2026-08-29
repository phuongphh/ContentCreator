import feedparser
import logging
from typing import Optional
from urllib.request import Request, urlopen

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import has_usable_content, insert_article, init_db

logger = logging.getLogger(__name__)

# User-Agent to avoid being blocked by feed servers
USER_AGENT = "ContentPipeline/1.0 (+https://github.com/content-pipeline)"


def _fetch_feed_content(feed_url: str) -> str:
    """Fetch feed content with proper User-Agent header."""
    req = Request(feed_url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/rss+xml, application/xml, text/xml, */*")
    with urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_content(entry) -> tuple[str, str]:
    """(summary, raw_content) từ 1 feed entry — lấy trường GIÀU nội dung nhất.

    feedparser đặt phần thân bài ở nhiều chỗ tuỳ feed: `content` (list, có thể
    nhiều mảnh), `summary`, `description`, `subtitle`. Bản cũ chỉ đọc
    `content[0]` rồi fallback `summary`, nên một feed để `content[0]` rỗng
    (hoặc chỉ chứa thẻ ảnh) cho ra article rỗng — nguồn gốc backlog bài "No
    content" của issue #117. Ở đây gộp mọi mảnh `content` và chọn theo độ dài
    sau khi gỡ HTML.
    """
    summary_raw = (entry.get("summary", "") or entry.get("description", "")
                   or entry.get("subtitle", "") or "")
    summary = _strip_html(summary_raw)[:500]

    parts = []
    content = entry.get("content") or []
    if isinstance(content, list):
        for item in content:
            value = item.get("value", "") if isinstance(item, dict) else str(item)
            cleaned = _strip_html(value or "")
            if cleaned:
                parts.append(cleaned)
    elif isinstance(content, str):
        cleaned = _strip_html(content)
        if cleaned:
            parts.append(cleaned)

    body = "\n\n".join(parts)
    # `content` rỗng/nghèo hơn summary → dùng summary làm nội dung (không để
    # raw_content ngắn hơn summary như bản cũ).
    raw_content = body if len(body) >= len(summary) else summary
    return summary, raw_content


def collect_feed(feed_url: str, max_articles: int = 20) -> int:
    """Collect articles from a single RSS feed. Returns count of new articles."""
    logger.info("Collecting from: %s", feed_url)
    try:
        # Fetch with User-Agent header to avoid 403/HTML responses
        content = _fetch_feed_content(feed_url)
        feed = feedparser.parse(content)
    except Exception as e:
        logger.warning("Feed error for %s: %s", feed_url, e)
        return 0

    if feed.bozo:
        if feed.entries:
            logger.warning(
                "Feed %s has parse warnings but %d entries found, continuing...",
                feed_url, len(feed.entries)
            )
        else:
            logger.warning(
                "Feed error for %s: %s (0 entries)",
                feed_url, feed.get("bozo_exception")
            )
            return 0

    source = feed.feed.get("title", feed_url)
    logger.info("Feed '%s' returned %d entries", source, len(feed.entries))
    count = 0
    skipped_no_title = 0
    skipped_no_url = 0
    skipped_no_content = 0
    skipped_duplicate = 0

    for entry in feed.entries[:max_articles]:
        title = entry.get("title", "").strip()
        url = entry.get("link", "").strip()
        if not title:
            skipped_no_title += 1
            continue
        if not url:
            skipped_no_url += 1
            continue

        summary, raw_content = _extract_content(entry)

        # Issue #117: entry chỉ có tiêu đề (feed không kèm summary/content, hoặc
        # summary chỉ là markup rỗng sau khi gỡ HTML) KHÔNG lưu vào DB — bài như
        # vậy không bao giờ phân tích sâu được, chỉ tồn đọng trong pool 'pending'
        # và chặn bài thật. Không insert (thay vì insert rồi đánh dấu skipped) để
        # feed bổ sung mô tả muộn vẫn còn cơ hội được thu thập lần chạy sau.
        if not has_usable_content(raw_content, summary):
            skipped_no_content += 1
            logger.debug("Skipping (không có nội dung, chỉ tiêu đề): %s", title[:60])
            continue

        article_id = insert_article(
            source=source,
            title=title,
            url=url,
            raw_content=raw_content,
            summary=summary,
        )
        if article_id:
            count += 1
        else:
            skipped_duplicate += 1

    if skipped_no_title or skipped_no_url or skipped_no_content or skipped_duplicate:
        logger.info(
            "Feed '%s' skipped: %d no title, %d no url, %d no content, %d duplicates",
            source, skipped_no_title, skipped_no_url, skipped_no_content, skipped_duplicate,
        )
    if skipped_no_content and not count:
        # Cả feed chỉ có tiêu đề = feed đổi format (hoặc chặn nội dung), không
        # phải "hôm nay không có tin" — nói rõ để không phải đọc log từng dòng.
        logger.warning(
            "Feed '%s': %d/%d entry KHÔNG có nội dung — feed có thể đã đổi format",
            source, skipped_no_content, len(feed.entries[:max_articles]),
        )
    logger.info("Collected %d new articles from %s", count, source)
    return count


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    import re
    import html
    clean = re.sub(r"<[^>]+>", "", text)
    clean = html.unescape(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def collect_all_feeds() -> int:
    """Collect from all configured RSS feeds. Returns total new articles."""
    total = 0
    for feed_url in config.RSS_FEEDS:
        try:
            count = collect_feed(feed_url)
            total += count
        except Exception as e:
            logger.error("Error collecting feed %s: %s", feed_url, e)
            continue
    logger.info("Total new articles from RSS: %d", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    collect_all_feeds()
