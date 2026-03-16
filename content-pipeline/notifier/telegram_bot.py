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


def send_telegram_message(text: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping notification.")
        return False

    url = (
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        f"/sendMessage?chat_id={config.TELEGRAM_CHAT_ID}"
        f"&parse_mode=HTML"
        f"&text={quote(text)}"
    )

    try:
        req = Request(url)
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Telegram message sent successfully.")
                return True
            logger.error("Telegram API returned status %d", resp.status)
            return False
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)
        return False


def build_report() -> str:
    """Build the daily report message."""
    today = date.today().strftime("%d/%m/%Y")
    articles = get_report_articles(config.SCORE_THRESHOLD_NOTIFY)

    immediate = articles.get("immediate", [])
    this_week = articles.get("this_week", [])
    backlog = articles.get("backlog", [])

    lines = [f"📊 BÁO CÁO CONTENT - {today}\n"]

    if immediate:
        lines.append(f"🔥 ĐĂNG NGAY ({len(immediate)} bài):")
        for i, a in enumerate(immediate, 1):
            analysis = json.loads(a["ai_analysis"]) if a.get("ai_analysis") else {}
            lines.append(
                f"{i}. [{a.get('ai_score', 0):.1f}/10] {a['title']}\n"
                f"   → Góc: {analysis.get('viet_angle', 'N/A')}\n"
                f"   → Loại: {a.get('category', 'N/A')}\n"
                f"   → Link: {a.get('url', '')}"
            )
        lines.append("")

    if this_week:
        lines.append(f"📅 TRONG TUẦN ({len(this_week)} bài):")
        for i, a in enumerate(this_week, 1):
            lines.append(f"{i}. [{a.get('ai_score', 0):.1f}/10] {a['title']}")
        lines.append("")

    backlog_count = len(backlog)
    if backlog_count:
        lines.append(f"💾 BACKLOG: {backlog_count} bài")

    if not immediate and not this_week and not backlog_count:
        lines.append("Không có bài viết nào đạt ngưỡng hôm nay.")

    return "\n".join(lines)


def send_daily_report() -> bool:
    """Build and send the daily report."""
    report = build_report()
    logger.info("Report:\n%s", report)
    return send_telegram_message(report)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    send_daily_report()
