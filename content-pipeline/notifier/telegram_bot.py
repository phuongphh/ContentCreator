import json
import logging
from datetime import date
from urllib.request import Request, urlopen
from urllib.parse import quote

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_report_articles

logger = logging.getLogger(__name__)

# Telegram message max length (UTF-8)
TELEGRAM_MAX_LENGTH = 4096


def send_telegram_message(text: str) -> bool:
    """Send a message via Telegram Bot API. Splits long messages if needed."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping notification.")
        return False

    # Split into chunks if too long
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
        # Find last newline before max_len
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def build_report() -> str:
    """Build the daily resume-style report — top 5 articles, direct and clear."""
    today = date.today().strftime("%d/%m/%Y")
    articles = get_report_articles(config.SCORE_THRESHOLD_NOTIFY)

    # Flatten all articles, sort by score descending, pick top N
    all_articles = []
    for group in articles.values():
        all_articles.extend(group)
    all_articles.sort(key=lambda a: a.get("ai_score", 0), reverse=True)

    top_n = getattr(config, "TOP_RESUME_COUNT", 5)
    top_articles = all_articles[:top_n]

    if not top_articles:
        return f"📊 AI 5 PHÚT — {today}\n\nKhông có bài viết nào đạt ngưỡng hôm nay."

    lines = [
        f"📊 <b>AI 5 PHÚT MỖI NGÀY — {today}</b>",
        f"Top {len(top_articles)} tin AI đáng đọc hôm nay:\n",
    ]

    for i, article in enumerate(top_articles, 1):
        analysis = {}
        if article.get("ai_analysis"):
            try:
                analysis = json.loads(article["ai_analysis"])
            except (json.JSONDecodeError, TypeError):
                pass

        score = article.get("ai_score", 0)
        title = article.get("title", "N/A")
        url = article.get("url", "")
        summary = analysis.get("one_line_summary", "")
        viet_angle = analysis.get("viet_angle", "")
        category = analysis.get("category", "")
        urgency = analysis.get("urgency", "")

        # Category emoji
        cat_emoji = {"tips": "💡", "news": "📰", "comparison": "⚖️"}.get(category, "📌")
        # Urgency tag
        urg_tag = {"immediate": "🔴 Nóng", "this_week": "🟡 Tuần này", "backlog": "🟢 Tham khảo"}.get(urgency, "")

        lines.append(f"{'─' * 30}")
        lines.append(f"{cat_emoji} <b>{i}. {title}</b>")
        lines.append(f"⭐ {score:.1f}/10 │ {urg_tag}")

        if summary:
            lines.append(f"\n{summary}")

        if viet_angle:
            lines.append(f"\n👉 <i>{viet_angle}</i>")

        if url:
            lines.append(f"\n🔗 {url}")

        lines.append("")

    # Footer
    remaining = len(all_articles) - len(top_articles)
    if remaining > 0:
        lines.append(f"📦 Còn {remaining} bài khác trong backlog.")

    lines.append(f"\n— AI 5 Phút Mỗi Ngày 🤖")

    return "\n".join(lines)


def send_daily_report() -> bool:
    """Build and send the daily report."""
    report = build_report()
    logger.info("Report:\n%s", report)
    return send_telegram_message(report)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    send_daily_report()
