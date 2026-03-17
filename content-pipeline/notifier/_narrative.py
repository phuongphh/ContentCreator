"""
Narrative Report Generator — Tạo bài tổng hợp 800 từ từ top articles.

Tách riêng từ telegram_bot.py để main.py có thể dùng trực tiếp.
"""

import json
import logging

import anthropic

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

NARRATIVE_PROMPT = """Bạn là biên tập viên kênh "AI 5 Phút Mỗi Ngày" — kênh YouTube/TikTok
giúp người Việt đi làm văn phòng (22-35 tuổi, không rành kỹ thuật) hiểu và dùng AI.

Từ {count} tin AI dưới đây, hãy viết MỘT bài tổng hợp khoảng 800 từ bằng tiếng Việt.

YÊU CẦU VỀ CẤU TRÚC:
1. MỞ ĐẦU (2-3 câu): Kịch tính, gây tò mò. Ví dụ: "Hôm nay thế giới AI lại xảy ra chuyện..."
   hoặc đặt câu hỏi khiến người đọc muốn đọc tiếp.
2. THÂN BÀI: Tóm tắt từng tin, mỗi tin 1 đoạn ngắn (3-5 câu). Giải thích đơn giản,
   liên hệ thực tế cho người đi làm tại Việt Nam. Dùng ngôn ngữ đời thường, tránh thuật ngữ.
3. KẾT LUẬN (3-5 câu): Rút ra bài học hoặc lời khuyên cụ thể cho nhân viên văn phòng.
   Ví dụ: nên thử tool nào, nên cẩn thận gì, xu hướng nào cần theo dõi.

YÊU CẦU VỀ PHONG CÁCH:
- Trực diện, không vòng vo
- Dễ hiểu — viết cho người không biết code
- Hấp dẫn như đang kể chuyện cho đồng nghiệp nghe lúc ăn trưa
- Không dùng emoji quá nhiều (tối đa 3-4 emoji cả bài)
- Không liệt kê dạng bullet point — viết thành văn xuôi mạch lạc
- Kết thúc bằng 1 câu đáng nhớ

DANH SÁCH TIN:
{articles_text}

Viết bài tổng hợp (chỉ trả về nội dung bài, không giải thích thêm):"""


def _build_articles_text(articles: list[dict]) -> str:
    """Format articles for the narrative prompt."""
    parts = []
    for i, article in enumerate(articles, 1):
        analysis = {}
        if article.get("ai_analysis"):
            try:
                analysis = json.loads(article["ai_analysis"])
            except (json.JSONDecodeError, TypeError):
                pass

        title = article.get("title", "N/A")
        summary = analysis.get("one_line_summary", article.get("summary", "")[:200])
        viet_angle = analysis.get("viet_angle", "")
        category = analysis.get("category", "")
        url = article.get("url", "")

        part = f"TIN {i}: {title}\n"
        part += f"Tóm tắt: {summary}\n"
        if viet_angle:
            part += f"Góc Việt Nam: {viet_angle}\n"
        part += f"Loại: {category}\n"
        part += f"Link: {url}\n"
        parts.append(part)

    return "\n".join(parts)


def generate_narrative_report(articles: list[dict]) -> str | None:
    """Call Claude Sonnet to generate an 800-word narrative report from top articles."""
    if not articles:
        return None

    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
        return None

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    articles_text = _build_articles_text(articles)
    prompt = NARRATIVE_PROMPT.format(count=len(articles), articles_text=articles_text)

    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            narrative = message.content[0].text.strip()
            logger.info("Generated narrative report (%d chars)", len(narrative))
            return narrative
        except anthropic.RateLimitError:
            import time
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited, waiting %ds...", wait)
            time.sleep(wait)
        except anthropic.APIError as e:
            logger.error("API error generating narrative: %s", e)
            return None

    logger.error("Failed to generate narrative after 3 attempts")
    return None
