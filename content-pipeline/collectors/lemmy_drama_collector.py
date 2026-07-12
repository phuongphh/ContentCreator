from __future__ import annotations

"""
Lemmy Drama Collector (issue #78 follow-up) — Reddit-alternative source.

Reddit killed self-service API app creation (Nov 2025), so the Drama track needs
a source that doesn't gate access behind a multi-week approval. Lemmy is a
federated, open Reddit alternative: its read API is public (no OAuth, no
approval, no key), so we can pull "top of day" posts from drama/relationship
communities directly.

Stories come out in English; the existing processors/drama_rewriter.py localizes
them into Vietnamese, same as it did for Reddit content — so this collector only
has to fetch + filter + insert into the `stories` table (track='drama'), exactly
like collectors/reddit_drama_collector.py.

API: GET {instance}/api/v3/post/list?community_name={name}&sort=TopDay&limit=50&type_=All
Response: {"posts": [{"post": {...}, "counts": {"score": N, ...}, ...}], ...}
No auth needed for public reads. Only stdlib (urllib) — no new dependency.
"""

import hashlib
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.stories import insert_story, dedupe_check

logger = logging.getLogger(__name__)

LISTING_LIMIT = 50  # Lemmy caps a single post/list page at 50.

# Removed/deleted post bodies (mirrors reddit_drama_collector) — treat as empty.
_REMOVED_SENTINELS = {"[removed]", "[deleted]"}


class LemmyFetchError(Exception):
    """A community listing could not be fetched (network/HTTP error), as opposed
    to fetching fine and finding 0 eligible posts. Lets collect_all_lemmy tell a
    total outage apart from a quiet empty day (same contract as the Reddit
    collector's RedditFetchError)."""


def collection_enabled() -> bool:
    """Whether the Lemmy collector should run (config.LEMMY_ENABLED)."""
    return bool(config.LEMMY_ENABLED)


def _source_id(post: dict) -> str:
    """Stable dedupe id for a Lemmy post.

    Derived from the federated `ap_id` (canonical cross-instance URL) so the same
    post seen via different instances dedupes to one story; falls back to the
    local numeric id when ap_id is missing.
    """
    ap_id = (post.get("ap_id") or "").strip()
    if ap_id:
        return "lemmy_" + hashlib.sha256(ap_id.encode("utf-8")).hexdigest()[:16]
    return f"lemmy_{post.get('id', '')}"


def parse_listing(raw_json) -> list[dict]:
    """Extract eligible-shape posts from a /post/list response. Pure function.

    Returns [] on an unexpected shape. Each dict carries everything the collector
    needs so there's no second per-post request.
    """
    try:
        raw_posts = raw_json["posts"]
    except (KeyError, TypeError):
        return []
    if not isinstance(raw_posts, list):
        return []

    posts: list[dict] = []
    for pv in raw_posts:
        if not isinstance(pv, dict):
            continue
        post = pv.get("post", {}) or {}
        counts = pv.get("counts", {}) or {}
        post_id = post.get("id")
        if post_id is None:
            continue
        posts.append({
            "id": post_id,
            "source_id": _source_id(post),
            "title": (post.get("name") or "").strip(),
            "body": post.get("body", "") or "",
            "score": counts.get("score", 0) or 0,
            "nsfw": bool(post.get("nsfw", False)),
            # Pinned posts are announcements/megathreads, not stories.
            "stickied": bool(post.get("featured_community", False)
                             or post.get("featured_local", False)),
            "removed": bool(post.get("removed", False) or post.get("deleted", False)),
            "url": (post.get("ap_id") or "").strip(),
        })
    return posts


def _fetch_community(community: str, sort: str = "TopDay") -> dict | None:
    """GET one community's listing. Returns parsed JSON, or None after retries."""
    query = urlencode({
        "community_name": community,
        "sort": sort,
        "limit": LISTING_LIMIT,
        "type_": "All",
    })
    url = f"{config.LEMMY_INSTANCE}/api/v3/post/list?{query}"
    last_error = None
    for attempt in range(config.LEMMY_MAX_RETRIES):
        req = Request(url)
        req.add_header("User-Agent", config.LEMMY_USER_AGENT)
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=config.LEMMY_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except HTTPError as e:
            last_error = e
            # 404 = community doesn't exist on this instance — retrying won't help.
            if e.code == 404:
                logger.warning("Lemmy community %s not found (404) on %s",
                               community, config.LEMMY_INSTANCE)
                return None
            logger.warning("Lemmy HTTP %s for %s (attempt %d/%d)",
                           e.code, community, attempt + 1, config.LEMMY_MAX_RETRIES)
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning("Lemmy request error for %s (attempt %d/%d): %s",
                           community, attempt + 1, config.LEMMY_MAX_RETRIES, e)
        if attempt < config.LEMMY_MAX_RETRIES - 1:
            time.sleep(2 ** (attempt + 1))
    logger.error("Lemmy fetch failed for %s after %d attempts: %s",
                 community, config.LEMMY_MAX_RETRIES, last_error)
    return None


def fetch_community_top(community: str) -> list[dict]:
    """Fetch + parse a community's top-of-day listing.

    Raises LemmyFetchError when the listing couldn't be fetched (network/HTTP
    failure), distinct from a successfully-fetched-but-empty listing ([]).
    """
    raw = _fetch_community(community)
    if raw is None:
        raise LemmyFetchError(f"listing fetch failed for {community}")
    posts = parse_listing(raw)
    logger.info("Lemmy %s returned %d posts", community, len(posts))
    return posts


def collect_community(community: str) -> int:
    """Collect eligible posts from one community into `stories`. Returns new count."""
    posts = fetch_community_top(community)
    count = 0
    skipped_dup = skipped_nsfw = skipped_low = skipped_removed = skipped_stickied = skipped_empty = 0

    for post in posts:
        if post["stickied"]:
            skipped_stickied += 1
            continue
        if post["nsfw"]:
            skipped_nsfw += 1
            continue
        if post["score"] < config.LEMMY_MIN_SCORE:
            skipped_low += 1
            continue
        body = post["body"].strip()
        if not body or body in _REMOVED_SENTINELS or post["removed"]:
            # Lemmy relationship/story posts live in the body; a link-only or
            # removed post has nothing to narrate.
            skipped_removed += 1 if (body in _REMOVED_SENTINELS or post["removed"]) else 0
            skipped_empty += 1 if not body else 0
            continue
        if dedupe_check(post["source_id"]):
            skipped_dup += 1
            continue

        insert_story(
            source="lemmy",
            source_id=post["source_id"],
            raw_content=body,
            track="drama",
            title=post["title"],
            metadata={
                "community": community,
                "score": post["score"],
                "url": post["url"],
            },
        )
        count += 1

    if any((skipped_dup, skipped_nsfw, skipped_low, skipped_removed, skipped_stickied, skipped_empty)):
        logger.info(
            "Lemmy %s skipped: %d dup, %d nsfw, %d below %d score, %d removed, "
            "%d stickied, %d empty",
            community, skipped_dup, skipped_nsfw, skipped_low, config.LEMMY_MIN_SCORE,
            skipped_removed, skipped_stickied, skipped_empty,
        )
    logger.info("Collected %d new drama stories from Lemmy %s", count, community)
    return count


def collect_all_lemmy() -> int:
    """Collect from every configured Lemmy community. Returns total new stories.

    Disabled (LEMMY_ENABLED=0) → returns 0 without touching the network. A single
    community failing is logged and skipped; if EVERY community fails to fetch
    (total outage), raises RuntimeError so the caller doesn't mistake an outage
    for a quiet "0 new stories" — same contract as collect_all_drama.
    """
    if not collection_enabled():
        logger.info("Lemmy collection disabled (LEMMY_ENABLED=0) — skipping")
        return 0

    communities = config.LEMMY_COMMUNITIES
    total = 0
    failures = 0
    for community in communities:
        try:
            total += collect_community(community)
        except Exception as e:
            logger.error("Error collecting Lemmy %s: %s", community, e)
            failures += 1
            continue
    if communities and failures == len(communities):
        raise RuntimeError(f"All {failures} Lemmy community(ies) failed to collect")
    logger.info("Total new drama stories from Lemmy: %d", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    from storage.database import init_db
    from storage.migrate import migrate_up
    init_db()
    migrate_up()
    collect_all_lemmy()
