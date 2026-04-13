from __future__ import annotations

"""
Content Pipeline Orchestrator — "AI 5 Phút Mỗi Ngày"

Pipeline hoàn chỉnh:
1. Thu thập tin AI từ nhiều nguồn
2. Lọc rule-based (miễn phí)
3. Chấm điểm AI (Haiku — rẻ)
4. Phân tích sâu AI (Sonnet)
5. Tạo script video (dài + ngắn)
6. TTS → audio
7. Subtitle + background → video
8. Gửi Telegram để duyệt
9. Approve → publish ngay lập tức

2 entry point:
- python main.py         → Chạy pipeline tạo video (launchd mỗi sáng 7:00)
- python main.py --bot   → Chạy Telegram bot liên tục (launchd daemon)
                            Bot lắng nghe /approve → publish ngay
"""

import argparse
import logging
import logging.handlers
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

import config
from storage.database import (
    init_db, get_top_analyzed_articles, insert_video,
    update_video_paths, update_video_status, get_video,
    update_video_publish_url,
)
from collectors.rss_collector import collect_all_feeds
from collectors.twitter_collector import collect_all_twitter
from collectors.reddit_collector import collect_all_reddit
from collectors.producthunt_collector import collect_producthunt
from processors.rule_filter import filter_pending_articles
from processors.ai_scorer import score_all_pending
from processors.ai_analyzer import analyze_top_articles
from video.script_generator import generate_long_script, generate_short_script
from video.tts_client import text_to_speech, get_audio_duration
from video.subtitle_generator import generate_srt
from video.video_composer import compose_video
from video.pexels_downloader import download_backgrounds, get_background
from publisher.scheduler import get_today_schedule, get_platform_label
from notifier.telegram_bot import (
    send_video_for_approval, send_publish_notification,
    send_pipeline_summary, send_narrative_report, run_bot,
)

# Ensure logs and output directories exist
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(config.VIDEO_OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            os.path.join(LOGS_DIR, "pipeline.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


def run_pipeline():
    """Run the full content + video pipeline."""
    logger.info("=== Pipeline started ===")
    init_db()
    errors = []

    # --- Phase 0: Ensure background videos exist ---
    logger.info("--- Phase 0: Background Videos ---")
    if not download_backgrounds():
        logger.warning("Background videos not ready — video composition may fail")

    # --- Phase 1: Content Collection & Analysis ---
    logger.info("--- Phase 1: Content Collection ---")
    total_new = 0
    for name, collector_fn in [
        ("RSS", collect_all_feeds),
        ("Twitter", collect_all_twitter),
        ("Reddit", collect_all_reddit),
        ("Product Hunt", collect_producthunt),
    ]:
        try:
            count = collector_fn()
            total_new += count
            logger.info("[%s] Collected %d new articles.", name, count)
        except Exception as e:
            logger.error("[%s] Collection failed: %s", name, e)
            errors.append(f"{name}: {e}")
    logger.info("Total new articles: %d", total_new)

    logger.info("--- Phase 1b: Filtering ---")
    try:
        kept = filter_pending_articles()
        logger.info("Kept %d articles after rule filtering.", kept)
    except Exception as e:
        logger.error("Rule filtering failed: %s", e)
        errors.append(f"Filter: {e}")

    logger.info("--- Phase 1c: AI Scoring ---")
    try:
        scored = score_all_pending()
        logger.info("Scored %d articles.", scored)
    except Exception as e:
        logger.error("AI scoring failed: %s", e)
        errors.append(f"Scoring: {e}")

    logger.info("--- Phase 1d: Deep Analysis ---")
    try:
        analyzed = analyze_top_articles()
        logger.info("Analyzed %d articles.", analyzed)
    except Exception as e:
        logger.error("AI analysis failed: %s", e)
        errors.append(f"Analysis: {e}")

    # --- Phase 2: Generate Narrative ---
    logger.info("--- Phase 2: Generate Narrative ---")
    top_n = getattr(config, "TOP_RESUME_COUNT", 5)
    articles = get_top_analyzed_articles(limit=top_n)

    if not articles:
        logger.warning("No articles available for video generation.")
        send_pipeline_summary(0, 0, errors + ["No articles for video"])
        logger.info("=== Pipeline completed (no content) ===")
        return

    from notifier._narrative import generate_narrative_report
    narrative = generate_narrative_report(articles)
    if not narrative:
        logger.error("Failed to generate narrative report")
        send_pipeline_summary(0, 0, errors + ["Narrative generation failed"])
        return

    # Send narrative summary to Telegram immediately — before video generation
    # so user always gets the daily summary even if video creation fails
    send_narrative_report(narrative, len(articles))
    logger.info("Narrative report sent to Telegram")

    # Mark articles as used so next run picks up fresh content
    from storage.database import mark_article_used
    for a in articles:
        mark_article_used(a["id"])
    logger.info("Marked %d articles as used", len(articles))

    # --- Phase 3: Video Generation ---
    logger.info("--- Phase 3: Video Generation ---")
    schedule = get_today_schedule()
    long_count, short_count = 0, 0

    if schedule is None:
        logger.info("Today is off — no video scheduled")
        send_pipeline_summary(0, 0, errors)
        logger.info("=== Pipeline completed (day off) ===")
        return

    video_type = schedule["video_type"]
    platforms = schedule["platforms"]
    date_str = schedule["scheduled_date"]

    if video_type == "long":
        vid_id = _create_video(narrative, "long", date_str, ", ".join(platforms))
        if vid_id:
            long_count = 1
        else:
            errors.append("Long video creation failed")
    else:
        vid_id = _create_video(narrative, "short", date_str, ", ".join(platforms))
        if vid_id:
            short_count = 1
        else:
            errors.append("Short video creation failed")

    send_pipeline_summary(long_count, short_count, errors)
    logger.info("=== Pipeline completed ===")


def _create_video(narrative: str, video_type: str, date_str: str,
                  platform: str) -> int | None:
    """Create a single video: script → TTS → subtitle → compose → send for approval."""
    logger.info("Creating %s video for %s...", video_type, date_str)

    # Step 1: Generate script
    if video_type == "long":
        script_data = generate_long_script(narrative)
    else:
        script_data = generate_short_script(narrative)

    if not script_data or not script_data.get("script"):
        logger.error("Script generation failed for %s video", video_type)
        return None

    script_text = script_data["script"]
    youtube_title = script_data.get("youtube_title", "")
    youtube_desc = script_data.get("youtube_description", "")
    tiktok_caption = script_data.get("tiktok_caption", "")
    tiktok_hashtags = script_data.get("tiktok_hashtags", "")

    # Step 2: Insert into DB
    video_id = insert_video(
        video_type=video_type,
        script_text=script_text,
        youtube_title=youtube_title,
        youtube_description=youtube_desc,
        tiktok_caption=tiktok_caption,
        tiktok_hashtags=tiktok_hashtags,
        scheduled_date=date_str,
        scheduled_platform=platform,
    )

    # Step 3: TTS
    subdir = "long" if video_type == "long" else "short"
    base_dir = os.path.join(config.VIDEO_OUTPUT_DIR, subdir, date_str)
    os.makedirs(base_dir, exist_ok=True)

    audio_path = os.path.join(base_dir, f"audio_{video_id}.mp3")
    result = text_to_speech(script_text, audio_path)
    if not result:
        logger.error("TTS failed for video %d", video_id)
        update_video_status(video_id, "draft")
        return None

    # Step 4: Subtitle
    duration = get_audio_duration(audio_path)
    if duration <= 0:
        logger.error("Cannot determine audio duration for video %d", video_id)
        return None

    srt_path = os.path.join(base_dir, f"subtitle_{video_id}.srt")
    result = generate_srt(script_text, duration, srt_path)
    if not result:
        logger.error("Subtitle generation failed for video %d", video_id)
        return None

    # Step 5: Select background + compose video
    orientation = "portrait" if video_type == "short" else "landscape"
    keywords = _extract_keywords(youtube_title, script_text)
    bg_video = get_background(keywords=keywords, orientation=orientation)
    if bg_video:
        logger.info("Using background: %s", bg_video)
    else:
        logger.warning("No background found — compose_video will use default")

    video_path = os.path.join(base_dir, f"video_{video_id}.mp4")
    result = compose_video(audio_path, srt_path, video_path,
                           video_type=video_type, bg_video=bg_video)
    if not result:
        logger.error("Video composition failed for video %d", video_id)
        return None

    # Update paths in DB
    update_video_paths(video_id, audio_path=audio_path,
                       subtitle_path=srt_path, video_path=video_path)
    update_video_status(video_id, "ready")

    # Step 6: Send for approval via Telegram
    send_video_for_approval(video_id)

    logger.info("Video %d created and sent for approval", video_id)
    return video_id


def _extract_keywords(title: str, script: str) -> list[str]:
    """Extract search keywords from video title/script for Pexels background search.

    Returns 2-3 short English queries suitable for stock video search.
    """
    keywords = []

    # Use the YouTube title as the primary keyword (most descriptive)
    if title:
        keywords.append(title)

    # Extract known AI/tech product names from the script for targeted search
    tech_terms = [
        "ChatGPT", "GPT", "Claude", "Gemini", "Copilot", "Midjourney",
        "Sora", "AI", "robot", "automation", "machine learning",
    ]
    script_lower = script.lower()
    for term in tech_terms:
        if term.lower() in script_lower:
            keywords.append(f"{term} technology")
            break  # One tech keyword is enough

    # Always include a generic fallback
    if len(keywords) < 2:
        keywords.append("artificial intelligence technology")

    return keywords[:3]


def publish_video(video_id: int):
    """Publish a single video to all its scheduled platforms.

    Called by the Telegram bot when user sends /approve.
    """
    video = get_video(video_id)
    if not video:
        logger.error("Video %d not found", video_id)
        return

    video_path = video.get("video_path")
    if not video_path or not os.path.exists(video_path):
        logger.error("Video file missing for video %d", video_id)
        return

    platforms = video.get("scheduled_platform", "").split(", ")

    for platform in platforms:
        try:
            url = _publish_to_platform(video, platform)
            if url:
                update_video_publish_url(video_id, url)
                update_video_status(video_id, "published")
                send_publish_notification(video_id, get_platform_label(platform), url)
                logger.info("Published video %d to %s: %s", video_id, platform, url)
            else:
                logger.error("Failed to publish video %d to %s", video_id, platform)
        except Exception as e:
            logger.error("Publish error for video %d on %s: %s", video_id, platform, e)


def _publish_to_platform(video: dict, platform: str) -> str | None:
    """Publish a video to a specific platform."""
    video_path = video["video_path"]

    if platform == "youtube":
        from publisher.youtube_uploader import upload_video
        return upload_video(
            video_path,
            title=video.get("youtube_title", "AI 5 Phút Mỗi Ngày"),
            description=video.get("youtube_description", ""),
            is_short=False,
        )
    elif platform == "youtube_shorts":
        from publisher.youtube_uploader import upload_video
        return upload_video(
            video_path,
            title=video.get("youtube_title", "AI 5 Phút"),
            description=video.get("youtube_description", ""),
            is_short=True,
        )
    elif platform == "tiktok":
        from publisher.tiktok_uploader import upload_video
        result = upload_video(
            video_path,
            caption=video.get("tiktok_caption", ""),
            hashtags=video.get("tiktok_hashtags", ""),
        )
        return f"tiktok://publish/{result}" if result else None
    else:
        logger.warning("Unknown platform: %s", platform)
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI 5 Phút Mỗi Ngày — Content Pipeline")
    parser.add_argument("--bot", action="store_true",
                        help="Run persistent Telegram bot (approve → publish instantly)")
    args = parser.parse_args()

    if args.bot:
        init_db()
        run_bot(publish_callback=publish_video)
    else:
        run_pipeline()
