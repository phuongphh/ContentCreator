import feedparser
import logging
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import insert_article, init_db

logger = logging.getLogger(__name__)


def collect_feed(feed_url: str, max_articles: int = 20) -> int:
    """Collect articles from a single RSS feed. Returns count of new articles."""
    logger.info("Collecting from: %s", feed_url)
    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        logger.error("Failed to parse feed %s: %s", feed_url, e)
        return 0

    if feed.bozo and not feed.entries:
        logger.warning("Feed error for %s: %s", feed_url, feed.get("bozo_exception"))
        return 0

    source = feed.feed.get("title", feed_url)
    count = 0

    for entry in feed.entries[:max_articles]:
        title = entry.get("title", "").strip()
        url = entry.get("link", "").strip()
        if not title or not url:
            continue

        summary = entry.get("summary", "") or entry.get("description", "")
        # Strip HTML tags simply
        summary = _strip_html(summary)[:500]

        raw_content = entry.get("content", [{}])
        if isinstance(raw_content, list) and raw_content:
            raw_content = raw_content[0].get("value", "")
        else:
            raw_content = summary

        article_id = insert_article(
            source=source,
            title=title,
            url=url,
            raw_content=raw_content,
            summary=summary,
        )
        if article_id:
            count += 1

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
