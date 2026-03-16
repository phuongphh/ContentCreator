"""
Content Pipeline Orchestrator — "AI 5 Phút Mỗi Ngày"

Chạy toàn bộ pipeline:
1. Thu thập từ RSS, Twitter, Reddit, Product Hunt
2. Lọc rule-based
3. Chấm điểm AI (Haiku)
4. Phân tích sâu AI (Sonnet)
5. Gửi báo cáo Telegram
"""

import logging
import logging.handlers
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import config
from storage.database import init_db
from collectors.rss_collector import collect_all_feeds
from collectors.twitter_collector import collect_all_twitter
from collectors.reddit_collector import collect_all_reddit
from collectors.producthunt_collector import collect_producthunt
from processors.rule_filter import filter_pending_articles
from processors.ai_scorer import score_all_pending
from processors.ai_analyzer import analyze_top_articles
from notifier.telegram_bot import send_daily_report

# Ensure logs directory exists
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            os.path.join(LOGS_DIR, "pipeline.log"),
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


def run_pipeline():
    """Run the full content pipeline."""
    logger.info("=== Pipeline started ===")

    # Step 0: Init DB
    init_db()

    # Step 1: Collect from all sources
    logger.info("--- Step 1: Collecting articles ---")
    total_new = 0

    collectors = [
        ("RSS", collect_all_feeds),
        ("Twitter", collect_all_twitter),
        ("Reddit", collect_all_reddit),
        ("Product Hunt", collect_producthunt),
    ]

    for name, collector_fn in collectors:
        try:
            count = collector_fn()
            total_new += count
            logger.info("[%s] Collected %d new articles.", name, count)
        except Exception as e:
            logger.error("[%s] Collection failed: %s", name, e)

    logger.info("Total new articles: %d", total_new)

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
