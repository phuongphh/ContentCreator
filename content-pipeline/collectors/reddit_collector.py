"""Reddit collector for the AI track (r/ChatGPT, r/artificial).

HTTP goes through collectors/reddit_client.py — OAuth app-only when credentials
are configured, unauthenticated fallback otherwise (issue #78). This is the same
client the Drama collector uses, so both tracks share one User-Agent, one token
cache, and one rate limiter.
"""

import logging

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collectors import reddit_client
from storage.database import insert_article, init_db

logger = logging.getLogger(__name__)

SUBREDDITS = ["ChatGPT", "artificial"]


def collect_subreddit(subreddit: str, limit: int = 20) -> int:
    """Collect hot posts from a subreddit. Returns count of new articles."""
    data = reddit_client.get_json(f"/r/{subreddit}/hot", {"limit": limit})
    if data is None:
        logger.error("Failed to fetch r/%s (blocked or network error)", subreddit)
        return 0

    posts = data.get("data", {}).get("children", [])
    logger.info("r/%s returned %d posts from API", subreddit, len(posts))
    count = 0
    skipped_dup = 0

    for post in posts:
        p = post.get("data", {})
        title = p.get("title", "").strip()
        permalink = p.get("permalink", "")
        selftext = p.get("selftext", "")
        post_url = f"https://www.reddit.com{permalink}" if permalink else p.get("url", "")

        if not title or not post_url:
            continue

        article_id = insert_article(
            source=f"Reddit/r/{subreddit}",
            title=title,
            url=post_url,
            raw_content=selftext or title,
            summary=(selftext[:500] if selftext else title),
        )
        if article_id:
            count += 1
        else:
            skipped_dup += 1

    if skipped_dup:
        logger.info("r/%s: %d duplicates skipped", subreddit, skipped_dup)
    logger.info("Collected %d new posts from r/%s", count, subreddit)
    return count


def collect_all_reddit() -> int:
    """Collect from all configured subreddits.

    Skips entirely when Reddit collection is disabled (issue #78): with no
    approved OAuth credentials we don't touch Reddit — the AI track keeps
    running on its RSS + other sources instead.
    """
    if not reddit_client.collection_enabled():
        logger.info("Reddit collection disabled (issue #78) — skipping AI subreddits")
        return 0
    total = 0
    for sub in SUBREDDITS:
        try:
            total += collect_subreddit(sub)
        except Exception as e:
            logger.error("Error collecting r/%s: %s", sub, e)
            continue
    logger.info("Total new Reddit posts: %d", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    collect_all_reddit()
