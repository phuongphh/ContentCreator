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
9. Publish sau khi approve

Có 3 entry point:
- python main.py              → Chạy pipeline tạo video (cronjob 7:00)
- python main.py --publish    → Publish video đã approve (cronjob mỗi 30 phút)
- python main.py --poll       → Poll Telegram cho approval (cronjob mỗi 5 phút)
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
    get_approved_videos_for_date, update_video_publish_url,
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
from publisher.scheduler import get_today_schedule, get_platform_label
from notifier.telegram_bot import (
    send_video_for_approval, poll_approvals,
    send_publish_notification, send_pipeline_summary,
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

    # Generate narrative (reuse the existing AI generation)
    from notifier._narrative import generate_narrative_report
    narrative = generate_narrative_report(articles)
    if not narrative:
        logger.error("Failed to generate narrative report")
        send_pipeline_summary(0, 0, errors + ["Narrative generation failed"])
        return

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
    """Create a single video: script → TTS → subtitle → compose → send for approval.

    Returns video_id on success, None on failure.
    """
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

    # Step 5: Compose video
    video_path = os.path.join(base_dir, f"video_{video_id}.mp4")
    result = compose_video(audio_path, srt_path, video_path, video_type=video_type)
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


def run_poll():
    """Poll Telegram for approval commands and process them."""
    logger.info("=== Polling for approvals ===")
    init_db()

    actions = poll_approvals()
    for action in actions:
        video_id = action["video_id"]
        if action["action"] == "approve":
            update_video_status(video_id, "approved")
            logger.info("Video %d approved", video_id)
        elif action["action"] == "reject":
            update_video_status(video_id, "rejected")
            logger.info("Video %d rejected", video_id)

    if actions:
        logger.info("Processed %d approval actions", len(actions))


def run_publish():
    """Publish all approved videos scheduled for today."""
    logger.info("=== Publishing approved videos ===")
    init_db()

    today_str = date.today().isoformat()
    videos = get_approved_videos_for_date(today_str)

    if not videos:
        logger.info("No approved videos to publish today")
        return

    for video in videos:
        video_id = video["id"]
        video_path = video.get("video_path")
        platforms = video.get("scheduled_platform", "").split(", ")

        if not video_path or not os.path.exists(video_path):
            logger.error("Video file missing for video %d", video_id)
            continue

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
    parser.add_argument("--publish", action="store_true", help="Publish approved videos")
    parser.add_argument("--poll", action="store_true", help="Poll Telegram for approvals")
    args = parser.parse_args()

    if args.publish:
        run_publish()
    elif args.poll:
        run_poll()
    else:
        run_pipeline()
