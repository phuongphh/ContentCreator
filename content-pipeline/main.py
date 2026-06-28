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

# Safety net: catch missing deps early with a clear error.
# ROOT CAUSE prevention is the venv via run_pipeline.sh — this just diagnoses
# quickly if someone bypasses the wrapper.
_REQUIRED = {
    "PIL": "Pillow",
    "googleapiclient": "google-api-python-client",
    "anthropic": "anthropic",
    "feedparser": "feedparser",
    "dotenv": "python-dotenv",
}
_missing = []
for _mod, _pkg in _REQUIRED.items():
    try:
        __import__(_mod)
    except ImportError:
        _missing.append(_pkg)
if _missing:
    print(
        f"[FATAL] Missing packages: {', '.join(_missing)}\n"
        f"Fix: cd {os.path.dirname(__file__)} && "
        "venv/bin/pip install -r requirements.txt\n"
        "Or use run_pipeline.sh instead of calling python directly.",
        file=sys.stderr,
    )
    sys.exit(1)
del _REQUIRED, _missing

import config
from storage.database import (
    init_db, get_top_analyzed_articles, insert_video,
    update_video_paths, update_video_status, get_video,
    update_video_publish_url, set_video_subtitles_burned,
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
from video.text_preprocessor import preprocess_for_tts
from video.subtitle_generator import generate_srt, write_entries_srt
from video.video_composer import compose_video
from video.pexels_downloader import (
    download_backgrounds, download_font, get_background, get_backgrounds,
)
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


def run_pipeline(force_video: str | None = None):
    """Run the full content + video pipeline.

    Args:
        force_video: If set to "long" or "short", bypass the scheduler and
                     generate a video of that type regardless of the day.
                     Useful for manual testing and catching up on missed days.
    """
    logger.info("=== Pipeline started ===")
    init_db()
    config.validate_flags(logger)
    errors = []

    # --- Phase 0: Ensure assets exist ---
    logger.info("--- Phase 0: Assets ---")
    if not download_backgrounds():
        logger.warning("Background videos not ready — video composition may fail")
    if not download_font():
        logger.warning("NotoSans font not ready — Vietnamese subtitles may not render correctly")

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
    long_count, short_count = 0, 0

    if force_video:
        # Manual override — bypass schedule, use today's date
        from datetime import date as _date
        video_type = force_video  # "long" or "short"
        platforms = (["youtube"] if video_type == "long"
                     else ["youtube_shorts", "tiktok"])
        date_str = _date.today().isoformat()
        logger.info("Force-video mode: creating %s video for %s", video_type, date_str)
    else:
        schedule = get_today_schedule()
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
    tts_text = preprocess_for_tts(script_text)
    result = text_to_speech(tts_text, audio_path)
    if not result:
        logger.error("TTS failed for video %d", video_id)
        update_video_status(video_id, "draft")
        return None

    # Step 3b: Background music (P1, optional) — mix under the narration.
    # Use an .m4a container: the mixer encodes AAC, which the mp3 muxer rejects.
    if config.ENABLE_BGM:
        from video.audio_mixer import mix_background_music
        mixed_path = os.path.join(base_dir, f"audio_bgm_{video_id}.m4a")
        audio_path = mix_background_music(audio_path, mixed_path)

    # Step 4: Subtitle — Whisper-aligned timing (P1) with word-count fallback.
    duration = get_audio_duration(audio_path)
    if duration <= 0:
        logger.error("Cannot determine audio duration for video %d", video_id)
        return None

    srt_path = os.path.join(base_dir, f"subtitle_{video_id}.srt")
    entries = None
    if config.SUBTITLE_TIMING_MODE == "whisper":
        try:
            from video.subtitle_aligner import align
            entries = align(audio_path, script_text)
        except Exception as e:  # never let alignment kill the pipeline
            logger.warning("Whisper alignment error: %s — using word-count", e)
            entries = None
    if entries:
        logger.info("Using Whisper-aligned subtitle timing (%d entries)", len(entries))
        result = write_entries_srt(entries, srt_path)
    else:
        result = generate_srt(script_text, duration, srt_path)
    if not result:
        logger.error("Subtitle generation failed for video %d", video_id)
        return None

    # Step 5: Select background(s) + compose video
    orientation = "portrait" if video_type == "short" else "landscape"
    keywords = _extract_keywords(youtube_title, script_text)
    bg_video = None
    bg_videos = None
    if config.BACKGROUND_MODE == "multi":
        bg_videos = get_backgrounds(keywords=keywords, orientation=orientation,
                                    audio_duration=duration,
                                    count=config.BG_CLIP_COUNT)
        logger.info("Multi-clip background: %d clips", len(bg_videos or []))
    else:
        bg_video = get_background(keywords=keywords, orientation=orientation,
                                  audio_duration=duration)
        if bg_video:
            logger.info("Using background: %s", bg_video)
        else:
            logger.warning("No background found — compose_video will use default")

    video_path = os.path.join(base_dir, f"video_{video_id}.mp4")
    # Subtitle policy: burn in only for video types selected by BURN_SUBTITLES.
    # The SRT is still generated/stored above so long videos can upload it as a
    # YouTube caption track at publish time instead of burning it in.
    burn = config.should_burn_subtitles(video_type)
    burn_srt = srt_path if burn else None
    if not burn:
        logger.info("Burn-in disabled for %s video — will use caption track", video_type)
    # Composer engine selector (P2): MoviePy optional, FFmpeg default.
    if config.COMPOSER_ENGINE == "moviepy":
        from video.composer_moviepy import compose as compose_fn
    else:
        compose_fn = compose_video
    result = compose_fn(audio_path, burn_srt, video_path,
                        video_type=video_type, bg_video=bg_video,
                        bg_videos=bg_videos)
    if not result and config.COMPOSER_ENGINE == "moviepy":
        logger.warning("MoviePy compose failed — falling back to ffmpeg engine")
        result = compose_video(audio_path, burn_srt, video_path,
                               video_type=video_type, bg_video=bg_video,
                               bg_videos=bg_videos)
    if not result:
        logger.error("Video composition failed for video %d", video_id)
        return None

    # Update paths in DB. Persist the burn decision made at render time so
    # publish-time caption logic doesn't depend on the current BURN_SUBTITLES.
    update_video_paths(video_id, audio_path=audio_path,
                       subtitle_path=srt_path, video_path=video_path)
    set_video_subtitles_burned(video_id, burn)
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
        from publisher.youtube_uploader import upload_video, upload_caption
        url = upload_video(
            video_path,
            title=video.get("youtube_title", "AI 5 Phút Mỗi Ngày"),
            description=video.get("youtube_description", ""),
            is_short=False,
        )
        if not url:
            return None

        # Use the burn decision recorded at render time; only fall back to the
        # current config for legacy rows that predate the persisted flag.
        burned_flag = video.get("subtitles_burned")
        if burned_flag is None:
            burned = config.should_burn_subtitles(video.get("video_type", "long"))
        else:
            burned = bool(burned_flag)

        # No burned-in subtitles → the caption track IS the subtitles, so a
        # failed/absent upload means the public video would have none. Treat
        # that as a publish failure so it's surfaced instead of silently
        # shipping an uncaptioned video.
        if not burned:
            srt = video.get("subtitle_path")
            if not srt or not os.path.exists(srt):
                logger.error("Video %d has no SRT for its caption track — "
                             "publish flagged as failed", video["id"])
                return None
            if not upload_caption(url, srt):
                logger.error("Caption upload failed for no-burn video %d "
                             "(uploaded at %s) — publish flagged as failed",
                             video["id"], url)
                return None
        return url
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
    parser.add_argument(
        "--force-video",
        choices=["long", "short"],
        metavar="TYPE",
        help=(
            "Bypass the day-off check and generate a video of this type (long|short). "
            "Useful for manual testing or catching up on a missed day."
        ),
    )
    args = parser.parse_args()

    if args.bot:
        init_db()
        run_bot(publish_callback=publish_video)
    else:
        run_pipeline(force_video=args.force_video)
