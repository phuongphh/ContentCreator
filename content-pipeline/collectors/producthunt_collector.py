"""Product Hunt collector using GraphQL API."""

import json
import logging
from urllib.request import Request, urlopen

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import insert_article, init_db

logger = logging.getLogger(__name__)

PRODUCTHUNT_API_URL = "https://api.producthunt.com/v2/api/graphql"

QUERY = """
{
  posts(order: VOTES, topic: "artificial-intelligence", first: 20) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        votesCount
        website
        topics {
          edges {
            node {
              name
            }
          }
        }
      }
    }
  }
}
"""


def collect_producthunt(max_posts: int = 20) -> int:
    """Collect top AI posts from Product Hunt. Returns count of new articles."""
    if not config.PRODUCTHUNT_API_TOKEN:
        logger.warning("PRODUCTHUNT_API_TOKEN not configured, skipping.")
        return 0

    payload = json.dumps({"query": QUERY}).encode("utf-8")

    req = Request(PRODUCTHUNT_API_URL, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {config.PRODUCTHUNT_API_TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.error("Product Hunt API error: %s", e)
        return 0

    edges = data.get("data", {}).get("posts", {}).get("edges", [])
    count = 0

    for edge in edges[:max_posts]:
        node = edge.get("node", {})
        name = node.get("name", "").strip()
        tagline = node.get("tagline", "").strip()
        description = node.get("description", "")
        url = node.get("url", "")
        website = node.get("website", "")
        votes = node.get("votesCount", 0)

        if not name or not url:
            continue

        summary = f"{tagline} ({votes} votes)"
        raw = f"{tagline}\n\n{description}" if description else tagline

        article_id = insert_article(
            source="ProductHunt",
            title=name,
            url=website or url,
            raw_content=raw,
            summary=summary[:500],
        )
        if article_id:
            count += 1

    logger.info("Collected %d new posts from Product Hunt", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    collect_producthunt()
