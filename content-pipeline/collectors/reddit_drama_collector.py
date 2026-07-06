from __future__ import annotations

"""
Reddit Drama Collector — cào top post từ các subreddit "drama" (Phase 2).

Khác với collectors/reddit_collector.py (dùng cho track AI, JSON API 'hot'),
collector này:
1. Dùng RSS (`/top/.rss?t=day`) để lấy danh sách post — nhẹ, không cần auth.
2. Gọi thêm JSON API (`/comments/{id}.json`) cho từng post để lấy `score`
   (upvotes) và `selftext` đầy đủ — RSS không có 2 trường này.
3. Lọc theo `min_upvotes` (khác nhau mỗi subreddit) và bỏ NSFW (`over_18`).
4. Lưu vào bảng `stories` (track='drama') qua storage/stories.py, dedupe theo
   `source_id`.

Lưu ý thiết kế (khác với phase-2-detailed.md mục 3.1): NSFW được lọc bằng
cờ `over_18` chính thức từ JSON detail response — không cố đoán từ RSS, vì
định dạng RSS của Reddit không có trường NSFW được document rõ ràng. Đằng
nào cũng phải gọi JSON API để lấy score/selftext, nên dùng luôn `over_18`
từ đó là nguồn duy nhất, đáng tin cậy hơn.
"""

import json
import logging
import re
import time
from urllib.request import Request, urlopen

import feedparser

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.stories import insert_story, dedupe_check

logger = logging.getLogger(__name__)

REDDIT_USER_AGENT = "ContentPipeline/1.0 (drama story collector; contact: admin@example.com)"

DRAMA_SUBREDDITS = [
    {"name": "AmItheAsshole", "min_upvotes": 5000, "weight": 1.5},
    {"name": "AskReddit", "min_upvotes": 10000, "weight": 1.0},
    {"name": "relationship_advice", "min_upvotes": 3000, "weight": 1.3},
    {"name": "MaliciousCompliance", "min_upvotes": 5000, "weight": 1.4},
    {"name": "ProRevenge", "min_upvotes": 3000, "weight": 1.4},
]

# Reddit JSON API detail fetch: rate limit + retry tuning.
DETAIL_MIN_INTERVAL_SECONDS = 2.0
DETAIL_MAX_RETRIES = 3
DETAIL_RETRY_BACKOFF_BASE = 2  # seconds: 2, 4, 8...

_POST_ID_FROM_LINK_RE = re.compile(r"/comments/([a-z0-9]+)/", re.IGNORECASE)
_POST_ID_FROM_FULLNAME_RE = re.compile(r"t3_([a-z0-9]+)", re.IGNORECASE)


class _RateLimiter:
    """Sleeps as needed so consecutive calls are >= min_interval apart."""

    def __init__(self, min_interval: float = DETAIL_MIN_INTERVAL_SECONDS):
        self.min_interval = min_interval
        self._last_call = 0.0

    def wait(self):
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()


_rate_limiter = _RateLimiter()


def _extract_post_id(entry: dict) -> str | None:
    """Extract the bare Reddit post id (e.g. 'abc123') from a feedparser entry."""
    link = entry.get("link", "") or ""
    m = _POST_ID_FROM_LINK_RE.search(link)
    if m:
        return m.group(1)
    entry_id = entry.get("id", "") or ""
    m = _POST_ID_FROM_FULLNAME_RE.search(entry_id)
    if m:
        return m.group(1)
    return None


def _fetch_rss_content(url: str) -> str:
    """Fetch RSS content with a proper User-Agent (network call)."""
    req = Request(url)
    req.add_header("User-Agent", REDDIT_USER_AGENT)
    with urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_rss_entries(rss_content: str) -> list[dict]:
    """Parse RSS/Atom content into a list of {post_id, title, link, summary}.

    Pure function (no network) — entries with no resolvable post id are
    skipped since we can't dedupe/detail-fetch them.
    """
    feed = feedparser.parse(rss_content)
    result = []
    for entry in feed.entries:
        post_id = _extract_post_id(entry)
        if not post_id:
            continue
        result.append({
            "post_id": post_id,
            "title": (entry.get("title", "") or "").strip(),
            "link": entry.get("link", "") or "",
            "summary": entry.get("summary", "") or "",
        })
    return result


def fetch_subreddit_rss(subreddit: str, sort: str = "top", period: str = "day") -> list[dict]:
    """Fetch + parse the RSS feed for a subreddit. Returns [] on any error."""
    url = f"https://www.reddit.com/r/{subreddit}/{sort}/.rss?t={period}"
    try:
        content = _fetch_rss_content(url)
    except Exception as e:
        logger.warning("Failed to fetch RSS for r/%s: %s", subreddit, e)
        return []
    entries = parse_rss_entries(content)
    logger.info("r/%s RSS returned %d entries", subreddit, len(entries))
    return entries


def _fetch_post_json(subreddit: str, post_id: str) -> dict | None:
    """Fetch raw JSON for a post's detail (network call), rate-limited + retried."""
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    last_error = None
    for attempt in range(DETAIL_MAX_RETRIES):
        _rate_limiter.wait()
        req = Request(url)
        req.add_header("User-Agent", REDDIT_USER_AGENT)
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            last_error = e
            wait = DETAIL_RETRY_BACKOFF_BASE ** (attempt + 1)
            logger.warning(
                "Attempt %d/%d fetching detail for %s/%s failed: %s",
                attempt + 1, DETAIL_MAX_RETRIES, subreddit, post_id, e,
            )
            if attempt < DETAIL_MAX_RETRIES - 1:
                time.sleep(wait)
    logger.error("Giving up on %s/%s after %d attempts: %s",
                 subreddit, post_id, DETAIL_MAX_RETRIES, last_error)
    return None


def parse_post_detail(raw_json) -> dict | None:
    """Extract {selftext, ups, over_18} from a /comments/{id}.json response.

    Pure function (no network). Returns None if the shape is unexpected.
    """
    try:
        post_data = raw_json[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None
    return {
        "selftext": post_data.get("selftext", "") or "",
        "ups": post_data.get("score", post_data.get("ups", 0)) or 0,
        "over_18": bool(post_data.get("over_18", False)),
    }


def collect_subreddit(sub_config: dict) -> int:
    """Collect eligible posts from one subreddit into `stories`. Returns new-story count."""
    name = sub_config["name"]
    min_upvotes = sub_config["min_upvotes"]
    weight = sub_config.get("weight", 1.0)

    entries = fetch_subreddit_rss(name)
    count = 0
    skipped_dup = 0
    skipped_nsfw = 0
    skipped_low_score = 0
    skipped_no_detail = 0

    for entry in entries:
        source_id = f"reddit_{entry['post_id']}"
        if dedupe_check(source_id):
            skipped_dup += 1
            continue

        detail = _fetch_post_json(name, entry["post_id"])
        if detail is None:
            skipped_no_detail += 1
            continue

        if detail["over_18"]:
            skipped_nsfw += 1
            continue
        if detail["ups"] < min_upvotes:
            skipped_low_score += 1
            continue

        raw_content = detail["selftext"] or entry["title"]
        insert_story(
            source="reddit",
            source_id=source_id,
            raw_content=raw_content,
            track="drama",
            title=entry["title"],
            metadata={
                "subreddit": name,
                "upvotes": detail["ups"],
                "url": entry["link"],
                "weight": weight,
            },
        )
        count += 1

    if skipped_dup or skipped_nsfw or skipped_low_score or skipped_no_detail:
        logger.info(
            "r/%s skipped: %d duplicates, %d nsfw, %d below %d upvotes, %d detail-fetch failed",
            name, skipped_dup, skipped_nsfw, skipped_low_score, min_upvotes, skipped_no_detail,
        )
    logger.info("Collected %d new drama stories from r/%s", count, name)
    return count


def collect_all_drama() -> int:
    """Collect from every configured drama subreddit. Returns total new stories."""
    total = 0
    for sub_config in DRAMA_SUBREDDITS:
        try:
            total += collect_subreddit(sub_config)
        except Exception as e:
            logger.error("Error collecting r/%s: %s", sub_config["name"], e)
            continue
    logger.info("Total new drama stories: %d", total)
    return total


if __name__ == "__main__":
    import logging.handlers

    logs_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.TimedRotatingFileHandler(
                os.path.join(logs_dir, "reddit_drama.log"),
                when="midnight", backupCount=14, encoding="utf-8",
            ),
        ],
    )
    from storage.database import init_db
    from storage.migrate import migrate_up
    from storage.collector_health import record_success
    init_db()
    migrate_up()  # idempotent — ensures stories.title/metadata + track columns exist
    collect_all_drama()
    # Completing without an uncaught exception IS the "success" the 2-day
    # staleness alert (storage/collector_health.py) checks for — 0 new
    # stories on a given day is normal, not a failure.
    record_success("reddit_drama")
