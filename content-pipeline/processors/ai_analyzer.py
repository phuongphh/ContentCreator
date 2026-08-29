from __future__ import annotations

import json
import logging
import re

import anthropic

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import (
    choose_article_content,
    count_analysis_candidates,
    get_articles_for_analysis,
    has_usable_content,
    mark_article_skipped,
    mark_articles_unanalyzable,
    update_analysis,
)

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Bạn là chuyên gia content creator về AI tại Việt Nam.
Phân tích bài viết sau và tạo content brief cho video YouTube/TikTok.

BÀI VIẾT:
TIÊU ĐỀ: {title}
NGUỒN: {source}
NỘI DUNG:
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


def analyze_article(full_content: str, title: str = "", source: str = "") -> dict | None:
    """Analyze an article using Claude Sonnet. Returns analysis dict or None.

    `title`/`source` là tuỳ chọn để giữ tương thích với caller cũ, nhưng nên
    truyền: bản cũ chỉ đưa `full_content` nên với RSS summary cụt, Sonnet mất
    luôn ngữ cảnh quan trọng nhất — chính cái tiêu đề đã được chấm điểm.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = ANALYSIS_PROMPT.format(
        title=(title or "(không có)").strip(),
        source=(source or "(không rõ)").strip(),
        full_content=full_content[:4000],
    )

    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            from processors.ai_usage import log_token_usage
            log_token_usage("ai_analyzer", None, message, ref_type="article")
            response_text = message.content[0].text.strip()
            # Extract JSON from markdown code blocks or raw text
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not json_match:
                raise json.JSONDecodeError("No JSON object found", response_text, 0)
            result = json.loads(json_match.group())
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
    """Analyze top-scored articles. Returns count of analyzed articles.

    Issue #117: trước mỗi lần chọn bài, dọn những bài KHÔNG BAO GIỜ phân tích
    được (đã chấm điểm nhưng không có nội dung) ra khỏi pool. Bản cũ chỉ `continue`
    khi gặp bài rỗng nên chúng nằm lại pool vĩnh viễn, chiếm trọn
    MAX_DEEP_ANALYSIS slot và làm pipeline ra 0 video mỗi ngày.
    """
    swept = mark_articles_unanalyzable()
    if swept:
        logger.warning(
            "Skipped %d article(s) with no usable content (chỉ có tiêu đề)", swept
        )

    articles = get_articles_for_analysis(
        threshold=config.SCORE_THRESHOLD_ANALYSIS,
        limit=config.MAX_DEEP_ANALYSIS,
    )
    analyzed = 0

    for article in articles:
        # Lớp phòng thủ 2 (bài đã được lọc ở SQL): nếu vẫn lọt bài rỗng thì
        # đánh dấu ngay để nó không quay lại pool ngày mai.
        if not has_usable_content(article.get("raw_content"), article.get("summary")):
            logger.warning("No content for article id=%d, marking skipped", article["id"])
            mark_article_skipped(article["id"])
            continue

        content = choose_article_content(article.get("raw_content"), article.get("summary"))
        result = analyze_article(
            content,
            title=article.get("title", ""),
            source=article.get("source", ""),
        )
        if result:
            update_analysis(article["id"], result)
            analyzed += 1

    logger.info("Analyzed %d/%d articles", analyzed, len(articles))
    return analyzed


def no_analysis_reason() -> str:
    """Câu giải thích cho pipeline summary khi Phase 1d ra 0 bài (issue #117).

    "Analyzed 0/10" từng chỉ nằm trong log nên pipeline im lặng 2 ngày liền
    không ra video. Ba trạng thái rất khác nhau — hết bài / bài không có nội
    dung / gọi model hỏng — nên phân biệt rõ để đọc 1 dòng là biết sửa ở đâu.
    """
    try:
        stats = count_analysis_candidates()
    except Exception as e:  # DB lỗi thì vẫn phải báo được là "0 bài"
        logger.warning("Không đọc được thống kê pool phân tích: %s", e)
        return "Analysis: 0 bài được phân tích"

    if stats["pending_scored"] == 0:
        return ("Analysis: 0 bài — không còn bài đã chấm điểm nào chờ phân tích "
                "(nguồn thu thập im lặng?)")
    if stats["usable"] == 0:
        return (f"Analysis: 0 bài — {stats['pending_scored']} bài chờ nhưng KHÔNG "
                "bài nào có nội dung (feed chỉ trả tiêu đề?)")
    return (f"Analysis: 0/{stats['usable']} bài dùng được phân tích thành công "
            "(lỗi gọi Sonnet?)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Phân tích sâu các bài đã chấm điểm (Phase 1d).")
    parser.add_argument(
        "--status", action="store_true",
        help="Chỉ in thống kê pool phân tích (KHÔNG gọi AI, không tốn tiền)")
    parser.add_argument(
        "--sweep", action="store_true",
        help="Chỉ dọn bài không thể phân tích ra khỏi pool (KHÔNG gọi AI)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.status:
        # Dùng ngay sau khi deploy để xem pool có bị nghẽn như issue #117 không.
        stats = count_analysis_candidates()
        print(f"Chờ phân tích: {stats['pending_scored']} bài "
              f"({stats['usable']} có nội dung dùng được, "
              f"{stats['above_threshold']} đạt ngưỡng "
              f"{config.SCORE_THRESHOLD_ANALYSIS}).")
    elif args.sweep:
        print(f"Đã đưa {mark_articles_unanalyzable()} bài không có nội dung "
              "ra khỏi pool.")
    else:
        analyze_top_articles()
