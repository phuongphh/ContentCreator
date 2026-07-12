from __future__ import annotations

"""
Reddit Drama Collector — cào top post từ các subreddit "drama" (Phase 2).

Khác với collectors/reddit_collector.py (track AI, listing 'hot'), collector này
lấy listing 'top' theo ngày và lọc theo `min_upvotes`/NSFW cho từng subreddit.

Kiến trúc HTTP (issue #78): mọi request đi qua collectors/reddit_client.py —
OAuth app-only khi có credentials (oauth.reddit.com, không bị chặn), fallback
www.reddit.com khi chưa cấu hình. Xem docstring reddit_client để hiểu root cause.

Lịch sử: bản Phase 2 dùng RSS (`/top/.rss`) rồi gọi thêm JSON detail cho TỪNG
post để lấy score/selftext/over_18 (RSS không có) — pattern 1-RSS-cộng-N-detail
vừa chậm (rate-limit 2s/detail) vừa dễ bị 403/429. Từ issue #78 chuyển hẳn sang
JSON listing `/r/{sub}/top`: một request đã mang đủ score/selftext/over_18, nên
bỏ được cả RSS lẫn vòng N detail call. NSFW vẫn lọc bằng cờ `over_18` chính thức
(giờ có sẵn ngay trong listing).
"""

import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collectors import reddit_client
from storage.stories import insert_story, dedupe_check

logger = logging.getLogger(__name__)

DRAMA_SUBREDDITS = [
    {"name": "AmItheAsshole", "min_upvotes": 5000, "weight": 1.5},
    {"name": "AskReddit", "min_upvotes": 10000, "weight": 1.0},
    {"name": "relationship_advice", "min_upvotes": 3000, "weight": 1.3},
    {"name": "MaliciousCompliance", "min_upvotes": 5000, "weight": 1.4},
    {"name": "ProRevenge", "min_upvotes": 3000, "weight": 1.4},
]

# How many top posts to request per subreddit. Reddit caps a single listing page
# at 100; the top-of-day list is short so this is plenty.
LISTING_LIMIT = 50

# Bodies for moderator/user-removed posts come back as these literal sentinels —
# truthy, so they'd otherwise be stored as if they were real story content.
_REMOVED_SENTINELS = {"[removed]", "[deleted]"}


class RedditFetchError(Exception):
    """A subreddit listing could not be fetched (block/network), as opposed to
    fetching fine and finding 0 eligible posts.

    This distinction matters for collector_health: a genuine "0 new stories
    today" is a success, but a fetch failure must NOT be recorded as one — see
    collect_all_drama(). reddit_client.get_json() deliberately returns None (not
    raising) to stay resilient for all callers; this collector re-raises that
    None so a total block propagates instead of masquerading as an empty day.
    """


def _permalink_url(permalink: str) -> str:
    """Turn a Reddit permalink path into an absolute URL (best-effort)."""
    if not permalink:
        return ""
    if permalink.startswith("http"):
        return permalink
    return f"https://www.reddit.com{permalink}"


def parse_listing(raw_json) -> list[dict]:
    """Extract posts from a /r/{sub}/top listing JSON response.

    Pure function (no network). Each returned dict carries everything the
    collector needs — post_id, title, link, selftext, ups, over_18, stickied —
    so there's no second per-post request. Returns [] on an unexpected shape.
    """
    try:
        children = raw_json["data"]["children"]
    except (KeyError, TypeError):
        return []
    if not isinstance(children, list):
        return []

    posts: list[dict] = []
    for child in children:
        data = child.get("data", {}) if isinstance(child, dict) else {}
        post_id = data.get("id")
        if not post_id:
            continue
        posts.append({
            "post_id": post_id,
            "title": (data.get("title") or "").strip(),
            "link": _permalink_url(data.get("permalink", "") or ""),
            "selftext": data.get("selftext", "") or "",
            "ups": data.get("score", data.get("ups", 0)) or 0,
            "over_18": bool(data.get("over_18", False)),
            "stickied": bool(data.get("stickied", False)),
        })
    return posts


def fetch_subreddit_top(subreddit: str, period: str = "day") -> list[dict]:
    """Fetch + parse the top listing for a subreddit.

    Raises RedditFetchError when the listing couldn't be fetched (get_json
    returned None — a 403 block or network failure), so the caller can tell that
    apart from a successfully-fetched-but-empty listing. A valid empty listing
    returns [].
    """
    raw = reddit_client.get_json(
        f"/r/{subreddit}/top", {"t": period, "limit": LISTING_LIMIT}
    )
    if raw is None:
        raise RedditFetchError(
            f"listing fetch failed for r/{subreddit} (blocked or network error)"
        )
    posts = parse_listing(raw)
    logger.info("r/%s top listing returned %d posts", subreddit, len(posts))
    return posts


def collect_subreddit(sub_config: dict) -> int:
    """Collect eligible posts from one subreddit into `stories`. Returns new-story count."""
    name = sub_config["name"]
    min_upvotes = sub_config["min_upvotes"]
    weight = sub_config.get("weight", 1.0)

    posts = fetch_subreddit_top(name)
    count = 0
    skipped_dup = 0
    skipped_nsfw = 0
    skipped_low_score = 0
    skipped_removed = 0
    skipped_stickied = 0

    for post in posts:
        # Pinned/announcement posts (megathreads, rules) aren't drama stories.
        if post["stickied"]:
            skipped_stickied += 1
            continue

        source_id = f"reddit_{post['post_id']}"
        if dedupe_check(source_id):
            skipped_dup += 1
            continue

        if post["over_18"]:
            skipped_nsfw += 1
            continue
        if post["ups"] < min_upvotes:
            skipped_low_score += 1
            continue
        if post["selftext"].strip() in _REMOVED_SENTINELS:
            skipped_removed += 1
            continue

        raw_content = post["selftext"] or post["title"]
        insert_story(
            source="reddit",
            source_id=source_id,
            raw_content=raw_content,
            track="drama",
            title=post["title"],
            metadata={
                "subreddit": name,
                "upvotes": post["ups"],
                "url": post["link"],
                "weight": weight,
            },
        )
        count += 1

    if skipped_dup or skipped_nsfw or skipped_low_score or skipped_removed or skipped_stickied:
        logger.info(
            "r/%s skipped: %d duplicates, %d nsfw, %d below %d upvotes, "
            "%d removed/deleted, %d stickied",
            name, skipped_dup, skipped_nsfw, skipped_low_score, min_upvotes,
            skipped_removed, skipped_stickied,
        )
    logger.info("Collected %d new drama stories from r/%s", count, name)
    return count


def collect_all_drama() -> int:
    """Collect from every configured drama subreddit. Returns total new stories.

    A single failing subreddit doesn't fail the run (logged and skipped — same
    resilience philosophy as the rest of this codebase). But if EVERY subreddit
    call raises — a systemic failure like a total network outage or a Reddit
    block (issue #78), not "just found nothing new today" — this raises
    RuntimeError instead of silently returning 0. That distinction matters to
    the caller: __main__ only calls collector_health.record_success() after this
    returns normally, so a fully-failed run correctly stays UNRECORDED and the
    2-day staleness alert can eventually catch it.

    A 403 block makes reddit_client.get_json return None; fetch_subreddit_top
    turns that into a RedditFetchError (rather than a quiet "0 stories"), so a
    total block counts as a total failure here and record_success is skipped —
    otherwise a persistent block would keep refreshing last_success and stay
    invisible to the staleness alert forever.

    When Reddit collection is disabled (issue #78 follow-up — Reddit killed
    self-service app creation, so most installs have no OAuth creds), this
    returns 0 immediately without touching the network. The Drama track then
    runs on manual seeds (notifier/seed_bot.py); the drama-backlog alert
    (storage/collector_health.check_drama_backlog) replaces the old collector
    staleness alert as the "channel is starving" signal.
    """
    if not reddit_client.collection_enabled():
        logger.info(
            "Reddit collection disabled (issue #78) — skipping drama subreddits; "
            "Drama track relies on manual seeds (/seed_vn, /seed_url)"
        )
        return 0

    total = 0
    failures = 0
    for sub_config in DRAMA_SUBREDDITS:
        try:
            total += collect_subreddit(sub_config)
        except Exception as e:
            logger.error("Error collecting r/%s: %s", sub_config["name"], e)
            failures += 1
            continue
    if DRAMA_SUBREDDITS and failures == len(DRAMA_SUBREDDITS):
        raise RuntimeError(
            f"All {failures} configured drama subreddit(s) failed to collect"
        )
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
    if not reddit_client.collection_enabled():
        # Reddit off (issue #78) — nothing to do, and NOT a "success" to record
        # (there's no live collector to be healthy). Drama health is tracked by
        # the backlog alert now, not this collector's freshness.
        logger.info("Reddit collection disabled — reddit_drama collector is a no-op")
    else:
        collect_all_drama()
        # Completing without an uncaught exception IS the "success" the 2-day
        # staleness alert (storage/collector_health.py) checks for — 0 new
        # stories on a given day is normal, not a failure.
        record_success("reddit_drama")
