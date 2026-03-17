import json
import logging
import re
from datetime import date
from urllib.request import Request, urlopen
from urllib.parse import quote

import anthropic

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_top_analyzed_articles

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096

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
    """Call Claude Haiku to generate an 800-word narrative report from top articles."""
    if not articles:
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


def build_report() -> str:
    """Build the daily narrative report — 800-word broadcast-style summary."""
    today = date.today().strftime("%d/%m/%Y")
    top_n = getattr(config, "TOP_RESUME_COUNT", 5)
    articles = get_top_analyzed_articles(limit=top_n)

    if not articles:
        return f"📊 AI 5 PHÚT MỖI NGÀY — {today}\n\nKhông có bài viết nào đạt ngưỡng hôm nay."

    # Generate narrative using AI
    narrative = generate_narrative_report(articles)

    if not narrative:
        # Fallback: simple list if AI generation fails
        return _build_fallback_report(today, articles)

    # Build final message
    header = f"📊 AI 5 PHÚT MỖI NGÀY — {today}\n\n"

    # Append source links at the end
    footer_lines = ["\n\n--- Nguồn ---"]
    for i, a in enumerate(articles, 1):
        url = a.get("url", "")
        title = a.get("title", "")[:50]
        if url:
            footer_lines.append(f"{i}. {title}\n   {url}")
    footer = "\n".join(footer_lines)

    return header + narrative + footer


def _build_fallback_report(today: str, articles: list[dict]) -> str:
    """Simple list report when AI narrative generation fails."""
    lines = [f"📊 AI 5 PHÚT MỖI NGÀY — {today}\n"]
    for i, a in enumerate(articles, 1):
        analysis = {}
        if a.get("ai_analysis"):
            try:
                analysis = json.loads(a["ai_analysis"])
            except (json.JSONDecodeError, TypeError):
                pass

        score = a.get("ai_score", 0)
        title = a.get("title", "N/A")
        summary = analysis.get("one_line_summary", "")
        url = a.get("url", "")

        lines.append(f"{i}. [{score:.1f}/10] {title}")
        if summary:
            lines.append(f"   {summary}")
        if url:
            lines.append(f"   {url}")
        lines.append("")

    return "\n".join(lines)


def send_telegram_message(text: str) -> bool:
    """Send a message via Telegram Bot API. Splits long messages if needed."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping notification.")
        return False

    chunks = _split_message(text, TELEGRAM_MAX_LENGTH)
    success = True

    for chunk in chunks:
        url = (
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
            f"/sendMessage?chat_id={config.TELEGRAM_CHAT_ID}"
            f"&parse_mode=HTML"
            f"&text={quote(chunk)}"
        )
        try:
            req = Request(url)
            with urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    logger.error("Telegram API returned status %d", resp.status)
                    success = False
        except Exception as e:
            logger.error("Failed to send Telegram message: %s", e)
            success = False

    if success:
        logger.info("Telegram message sent successfully.")
    return success


def _split_message(text: str, max_len: int) -> list[str]:
    """Split a long message into chunks at line boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def send_daily_report() -> bool:
    """Build and send the daily report."""
    report = build_report()
    logger.info("Report:\n%s", report)
    return send_telegram_message(report)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    send_daily_report()
