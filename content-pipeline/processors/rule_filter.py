import logging

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import get_connection

logger = logging.getLogger(__name__)

RELEVANT_KEYWORDS = [
    # English
    "chatgpt", "claude", "gemini", "gpt-4", "gpt-5", "llm",
    "ai tool", "ai feature", "productivity", "workflow", "automation",
    "prompt", "copilot", "midjourney", "sora", "runway",
    # Vietnamese
    "trí tuệ nhân tạo", "công cụ ai", "ai tạo sinh",
]

SKIP_KEYWORDS = [
    "arxiv", "paper", "dataset", "benchmark", "fine-tuning",
    "huggingface", "github repo", "open source weights",
    "fundraising", "valuation", "lawsuit", "regulation", "policy",
    "acquisition", "merger", "ipo",
]


def filter_article(title: str, summary: str) -> bool:
    """Return True if the article should be kept, False to skip."""
    text = f"{title} {summary}".lower()

    # Skip if contains any skip keyword
    for kw in SKIP_KEYWORDS:
        if kw in text:
            logger.debug("Skipping (skip keyword '%s'): %s", kw, title[:60])
            return False

    # Keep only if contains at least one relevant keyword
    for kw in RELEVANT_KEYWORDS:
        if kw in text:
            return True

    logger.debug("Skipping (no relevant keyword): %s", title[:60])
    return False


def filter_pending_articles() -> int:
    """Filter all pending articles. Mark irrelevant ones as 'skipped'. Returns kept count."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, summary FROM articles WHERE status = 'pending' AND ai_score IS NULL"
        ).fetchall()

        kept = 0
        skipped = 0
        for row in rows:
            if filter_article(row["title"] or "", row["summary"] or ""):
                kept += 1
            else:
                conn.execute("UPDATE articles SET status = 'skipped' WHERE id = ?", (row["id"],))
                skipped += 1

        conn.commit()
        logger.info("Rule filter: kept %d, skipped %d", kept, skipped)
        return kept
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = filter_pending_articles()
    print(f"Kept {count} articles after rule filtering.")
