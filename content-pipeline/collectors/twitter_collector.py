"""Twitter/X collector using Twitter API v2."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote
import json

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import insert_article, init_db

logger = logging.getLogger(__name__)

TWITTER_API_BASE = "https://api.twitter.com/2"


def _api_request(endpoint: str, params: dict) -> dict | None:
    """Make an authenticated request to the Twitter API v2."""
    if not config.TWITTER_BEARER_TOKEN:
        logger.warning("TWITTER_BEARER_TOKEN not configured.")
        return None

    query_string = urlencode(params, quote_via=quote)
    url = f"{TWITTER_API_BASE}/{endpoint}?{query_string}"

    req = Request(url)
    req.add_header("Authorization", f"Bearer {config.TWITTER_BEARER_TOKEN}")

    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error("Twitter API error for %s: %s", endpoint, e)
        return None


def get_user_id(username: str) -> str | None:
    """Look up a Twitter user ID by username."""
    data = _api_request(f"users/by/username/{username}", {})
    if data and "data" in data:
        return data["data"]["id"]
    logger.warning("Could not find user: %s", username)
    return None


def collect_user_tweets(username: str, max_results: int = 10) -> int:
    """Collect recent tweets from a user. Returns count of new articles."""
    user_id = get_user_id(username)
    if not user_id:
        return 0

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "max_results": min(max_results, 100),
        "start_time": since,
        "tweet.fields": "created_at,text,public_metrics",
    }

    data = _api_request(f"users/{user_id}/tweets", params)
    if not data or "data" not in data:
        return 0

    count = 0
    for tweet in data["data"]:
        tweet_url = f"https://twitter.com/{username}/status/{tweet['id']}"
        article_id = insert_article(
            source=f"Twitter/@{username}",
            title=tweet["text"][:120],
            url=tweet_url,
            raw_content=tweet["text"],
            summary=tweet["text"][:300],
        )
        if article_id:
            count += 1

    logger.info("Collected %d tweets from @%s", count, username)
    return count


def collect_all_twitter() -> int:
    """Collect tweets from all configured accounts."""
    total = 0
    for username in config.TWITTER_ACCOUNTS:
        try:
            total += collect_user_tweets(username)
        except Exception as e:
            logger.error("Error collecting @%s: %s", username, e)
            continue
    logger.info("Total new tweets: %d", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    collect_all_twitter()
