"""Reddit collector using Reddit's JSON API (no auth required for public subreddits)."""

import logging
import json
from urllib.request import Request, urlopen

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import insert_article, init_db

logger = logging.getLogger(__name__)

SUBREDDITS = ["ChatGPT", "artificial"]
REDDIT_USER_AGENT = "ContentPipeline/1.0 (content curation bot; contact: admin@example.com)"


def collect_subreddit(subreddit: str, limit: int = 20) -> int:
    """Collect hot posts from a subreddit. Returns count of new articles."""
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
    req = Request(url)
    req.add_header("User-Agent", REDDIT_USER_AGENT)

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.error("Failed to fetch r/%s: %s", subreddit, e)
        return 0

    posts = data.get("data", {}).get("children", [])
    count = 0

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

    logger.info("Collected %d new posts from r/%s", count, subreddit)
    return count


def collect_all_reddit() -> int:
    """Collect from all configured subreddits."""
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
