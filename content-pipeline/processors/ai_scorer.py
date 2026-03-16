from __future__ import annotations

import json
import logging

import anthropic

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_pending_articles, update_score

logger = logging.getLogger(__name__)

SCORE_PROMPT = """Bạn là content strategist cho kênh YouTube/TikTok về AI dành cho
người Việt đi làm văn phòng (22-35 tuổi, không rành kỹ thuật).
Định vị kênh: "Giúp người Việt đi làm hiểu và dùng AI trong 5 phút"

Chấm điểm bài viết sau từ 1-10 theo 4 tiêu chí:
1. Người đi làm bình thường có quan tâm không? (1-10)
2. Có thể làm theo/áp dụng ngay hôm nay không? (1-10)
3. Giải thích được trong 5 phút không? (1-10)
4. Có gây cảm xúc (tò mò/hữu ích/lo lắng) không? (1-10)

TIÊU ĐỀ: {title}
TÓM TẮT: {summary}

Trả lời CHỈ bằng JSON, không giải thích thêm:
{{"score_1": <số>, "score_2": <số>, "score_3": <số>, "score_4": <số>, "total": <trung bình cộng>}}"""


def score_article(title: str, summary: str) -> float | None:
    """Score an article using Claude Haiku. Returns total score or None on error."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # Truncate summary to 300 chars to save tokens
    truncated_summary = summary[:300] if summary else ""

    prompt = SCORE_PROMPT.format(title=title, summary=truncated_summary)

    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text.strip()
            result = json.loads(response_text)
            # Calculate total if not provided
            if "total" in result:
                total = float(result["total"])
            else:
                scores = [float(result[f"score_{i}"]) for i in range(1, 5)]
                total = sum(scores) / len(scores)
            logger.info("Scored %.1f: %s", total, title[:60])
            return total
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("Attempt %d: Failed to parse score for '%s': %s", attempt + 1, title[:60], e)
            continue
        except anthropic.RateLimitError:
            import time
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited, waiting %ds before retry...", wait)
            time.sleep(wait)
            continue
        except anthropic.APIError as e:
            logger.error("API error scoring '%s': %s", title[:60], e)
            return None
    logger.error("Failed to score after 3 attempts: %s", title[:60])
    return None


def score_all_pending() -> int:
    """Score all pending articles. Returns count of scored articles."""
    articles = get_pending_articles(limit=config.MAX_ARTICLES_PER_RUN)
    scored = 0

    for article in articles:
        total = score_article(article["title"], article.get("summary", ""))
        if total is not None:
            update_score(article["id"], total)
            scored += 1

    logger.info("Scored %d/%d articles", scored, len(articles))
    return scored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    score_all_pending()
