import json
import logging

import anthropic

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_articles_for_analysis, update_analysis

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Bạn là chuyên gia content creator về AI tại Việt Nam.
Phân tích bài viết sau và tạo content brief cho video YouTube/TikTok.

BÀI VIẾT:
{full_content}

Tạo JSON với cấu trúc sau:
{{
  "category": "tips|news|comparison",
  "urgency": "immediate|this_week|backlog",
  "hooks": ["hook 1", "hook 2", "hook 3"],
  "viet_angle": "Cách Việt hoá và liên hệ thực tế cho người đi làm VN",
  "youtube_titles": ["title 1", "title 2", "title 3"],
  "tiktok_hashtags": ["#tag1", "#tag2"],
  "production_difficulty": "easy|medium|hard",
  "difficulty_reason": "lý do ngắn gọn",
  "one_line_summary": "tóm tắt 1 câu bằng tiếng Việt"
}}

Trả lời CHỈ bằng JSON, không giải thích thêm."""


def analyze_article(full_content: str) -> dict | None:
    """Analyze an article using Claude Sonnet. Returns analysis dict or None."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = ANALYSIS_PROMPT.format(full_content=full_content[:4000])

    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text.strip()
            result = json.loads(response_text)
            logger.info("Analyzed article, category: %s, urgency: %s",
                         result.get("category"), result.get("urgency"))
            return result
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("Attempt %d: Failed to parse analysis: %s", attempt + 1, e)
            continue
        except anthropic.RateLimitError:
            import time
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited, waiting %ds before retry...", wait)
            time.sleep(wait)
            continue
        except anthropic.APIError as e:
            logger.error("API error during analysis: %s", e)
            return None
    logger.error("Failed to analyze after 3 attempts")
    return None


def analyze_top_articles() -> int:
    """Analyze top-scored articles. Returns count of analyzed articles."""
    articles = get_articles_for_analysis(
        threshold=config.SCORE_THRESHOLD_ANALYSIS,
        limit=config.MAX_DEEP_ANALYSIS,
    )
    analyzed = 0

    for article in articles:
        content = article.get("raw_content") or article.get("summary", "")
        if not content:
            logger.warning("No content for article id=%d, skipping", article["id"])
            continue

        result = analyze_article(content)
        if result:
            update_analysis(article["id"], result)
            analyzed += 1

    logger.info("Analyzed %d/%d articles", analyzed, len(articles))
    return analyzed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    analyze_top_articles()
