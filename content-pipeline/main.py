"""
Content Pipeline Orchestrator — "AI 5 Phút Mỗi Ngày"

Chạy toàn bộ pipeline:
1. Thu thập RSS
2. Lọc rule-based
3. Chấm điểm AI (Haiku)
4. Phân tích sâu AI (Sonnet)
5. Gửi báo cáo Telegram
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import config
from storage.database import init_db
from collectors.rss_collector import collect_all_feeds
from processors.rule_filter import filter_pending_articles
from processors.ai_scorer import score_all_pending
from processors.ai_analyzer import analyze_top_articles
from notifier.telegram_bot import send_daily_report

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "pipeline.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def run_pipeline():
    """Run the full content pipeline."""
    logger.info("=== Pipeline started ===")

    # Step 0: Init DB
    init_db()

    # Step 1: Collect RSS feeds
    logger.info("--- Step 1: Collecting RSS feeds ---")
    try:
        new_articles = collect_all_feeds()
        logger.info("Collected %d new articles from RSS.", new_articles)
    except Exception as e:
        logger.error("RSS collection failed: %s", e)
        new_articles = 0

    # Step 2: Rule-based filtering
    logger.info("--- Step 2: Rule-based filtering ---")
    try:
        kept = filter_pending_articles()
        logger.info("Kept %d articles after rule filtering.", kept)
    except Exception as e:
        logger.error("Rule filtering failed: %s", e)

    # Step 3: AI Scoring (Haiku)
    logger.info("--- Step 3: AI Scoring (Haiku) ---")
    try:
        scored = score_all_pending()
        logger.info("Scored %d articles.", scored)
    except Exception as e:
        logger.error("AI scoring failed: %s", e)

    # Step 4: Deep analysis (Sonnet)
    logger.info("--- Step 4: Deep Analysis (Sonnet) ---")
    try:
        analyzed = analyze_top_articles()
        logger.info("Analyzed %d articles.", analyzed)
    except Exception as e:
        logger.error("AI analysis failed: %s", e)

    # Step 5: Telegram report
    logger.info("--- Step 5: Sending Telegram report ---")
    try:
        send_daily_report()
    except Exception as e:
        logger.error("Telegram notification failed: %s", e)

    logger.info("=== Pipeline completed ===")


if __name__ == "__main__":
    run_pipeline()
