from __future__ import annotations

"""
Drama Track Orchestrator (Phase 5 EPIC #5.4) — pipeline end-to-end:

1. Collect  — nguồn story cho track Drama. Reddit collection TẮT mặc định
              (issue #78: Reddit khoá tạo app tự phục vụ 11/2025), nên nguồn
              chính là seed thủ công qua Telegram (notifier/seed_bot.py:
              /seed_vn, /seed_url). Bật lại Reddit = REDDIT_ENABLED=1 + OAuth
              creds đã được duyệt. Bước này an toàn khi rỗng — score/rewrite/
              render đọc thẳng từ bảng `stories` nên seed nào cũng chảy qua.
2. Score    — rubric 6 tiêu chí bằng Haiku (Phase 3, processors/drama_scorer.py)
3. Rewrite  — Việt hoá bằng Sonnet (Phase 3, processors/drama_rewriter.py)
4. Render   — TTS + phụ đề + multi-scene composer (Phase 4, video/drama_composer.py)
5. Review   — push preview + nút ✅/❌/✏️ (Phase 5, notifier/review_bot.py)
6. Schedule — khi ✅, bot xếp lịch qua scheduler/post_scheduler.py (Phase 5)

Resume-from-crash: mỗi bước đọc trạng thái từ DB thay vì nhớ trong RAM —
story đi 'pending' → (scored) → 'approved' → 'produced'; video đi 'draft' →
'ready' → 'pending_approval' → 'approved' → 'published'. Chạy lại sau crash:
- collector dedupe theo source_id (Phase 2);
- scorer/rewriter chỉ nhặt story chưa có rubric_score/rewritten_content;
- render bỏ qua story đã có video row (videos.story_id, migration 006) — một
  lần crash giữa render không bao giờ tạo 2 video cho 1 story;
- upload dedupe nằm ở scheduler (claim + platform_video_id).

Chạy: python main_drama.py [--step collect|score|rewrite|render] [--limit N]
(không có --step = chạy đủ các bước).
"""

import argparse
import json
import logging
import logging.handlers
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(__file__))

import config
from storage.database import (
    init_db, insert_video, update_video_paths, update_video_status,
    update_video_metadata, get_videos_by_story, set_video_subtitles_burned,
)
from storage.stories import get_by_status, update_status

LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            os.path.join(LOGS_DIR, "drama_pipeline.log"),
            maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

DRAMA_DESTINATION = "drama_youtube"  # khoá trong channels.py


def build_narration(rewrite: dict) -> str:
    """Ghép text TTS từ output rewriter: hook + script + vn_commentary.

    Prompt rewriter (prompts/drama/rewriter.v1.txt) trả hook/vn_commentary
    thành FIELD RIÊNG nhưng script cũng có cấu trúc "Hook → ... → Reflection",
    nên model có thể đã lặp hook/commentary bên trong script. Kiểm tra
    containment trước khi ghép để không đọc trùng 2 lần.

    Mỗi phần được gỡ artifact phi-giọng-đọc (delimiter/markdown lọt từ LLM)
    TRƯỚC khi ghép: narration này vừa là input TTS vừa được lưu làm
    script_text cho phụ đề — sanitize ở đây giữ audio và phụ đề khớp nhau.
    """
    from video.text_preprocessor import strip_nonspeech_artifacts
    script = strip_nonspeech_artifacts((rewrite.get("script") or "").strip())
    hook = strip_nonspeech_artifacts((rewrite.get("hook") or "").strip())
    commentary = strip_nonspeech_artifacts((rewrite.get("vn_commentary") or "").strip())

    parts = []
    if hook and hook not in script:
        parts.append(hook)
    if script:
        parts.append(script)
    if commentary and commentary not in script:
        parts.append(commentary)
    return "\n\n".join(parts)


def _repush_stuck_reviews() -> int:
    """Gửi duyệt lại video drama đã render xong nhưng chưa tới tay reviewer.

    push_review() lỗi (Telegram down lúc render) để video kẹt 'ready' trong
    khi story đã 'produced' — resume guard chặn render lại nên không gì tự
    đẩy video đó vào flow duyệt nữa (finding của Codex review PR #70). Mỗi
    lần chạy render, thử push lại các video như vậy; thành công thì
    push_review tự chuyển 'pending_approval' nên không bao giờ push trùng.
    """
    from storage.database import get_videos_by_status
    from notifier.review_bot import push_review
    count = 0
    for video in get_videos_by_status("ready"):
        if video.get("track") != "drama":
            continue
        if push_review(video["id"]):
            count += 1
        else:
            logger.warning("Re-push review failed again for video %d", video["id"])
    if count:
        logger.info("Re-pushed %d stuck 'ready' drama video(s) for review", count)
    return count


def render_approved_stories(limit: int | None = None) -> list[int]:
    """Render các story 'approved' (đã Việt hoá) thành video + push review.

    Returns list video_id đã tạo. Story đã có video (resume guard) bị bỏ qua
    không tính vào limit.
    """
    limit = limit if limit is not None else config.DRAMA_VIDEOS_PER_RUN
    _repush_stuck_reviews()
    stories = get_by_status("approved", limit=limit * 3, track="drama")
    created: list[int] = []
    for story in stories:
        if len(created) >= limit:
            break
        # Resume guard: row 'failed' (lỗi transient đã phát hiện — TTS chết,
        # ffmpeg lỗi) KHÔNG chặn retry; mọi row khác (kể cả 'draft' do crash
        # giữa render — không biết đã đi tới đâu) chặn auto-render lại, chờ
        # người xử lý tay.
        existing = [v for v in get_videos_by_story(story["id"])
                    if v.get("status") not in ("failed", "rejected")]
        if existing:
            logger.info("Story %d already has video %s — skipping (resume guard)",
                        story["id"], [v["id"] for v in existing])
            continue
        video_id = _render_story(story)
        if video_id:
            created.append(video_id)
    logger.info("Rendered %d drama video(s): %s", len(created), created)
    return created


def _render_story(story: dict) -> int | None:
    """Render 1 story: TTS → subtitle → compose → DB → push review.

    Thứ tự ghi DB cho resume an toàn: insert video row (gắn story_id) TRƯỚC
    khi render — crash giữa render để lại row 'draft' + story vẫn 'approved',
    lần chạy sau resume guard thấy row là bỏ qua (người xử lý tay row draft
    mồ côi, thay vì pipeline tự render trùng). Lỗi transient PHÁT HIỆN ĐƯỢC
    (TTS/ffmpeg trả lỗi) thì row chuyển 'failed' → lần chạy sau tự retry.
    Story chỉ chuyển 'produced' sau khi render xong toàn bộ.
    """
    def _fail(video_id: int, why: str) -> None:
        # Lỗi transient đã phát hiện → row 'failed' để lần chạy sau tự retry
        # (resume guard bỏ qua row failed); khác với crash (row kẹt 'draft').
        logger.error("%s — video %d marked failed for retry", why, video_id)
        update_video_status(video_id, "failed")

    story_id = story["id"]
    try:
        rewrite = json.loads(story.get("rewritten_content") or "")
    except (json.JSONDecodeError, TypeError):
        logger.error("Story %d has malformed rewritten_content — flagging needs_review",
                     story_id)
        update_status(story_id, "needs_review")
        return None

    narration = build_narration(rewrite)
    if not narration:
        logger.error("Story %d has empty narration — flagging needs_review", story_id)
        update_status(story_id, "needs_review")
        return None

    title = rewrite.get("title", "") or (story.get("title") or "")
    tags = rewrite.get("tags") or []
    hashtags = " ".join(f"#{str(t).strip().lstrip('#').replace(' ', '')}"
                        for t in tags if str(t).strip())

    video_id = insert_video(
        video_type="short",
        script_text=narration,
        youtube_title=title,
        youtube_description=(rewrite.get("hook", "") or title),
        tiktok_caption=title,
        tiktok_hashtags=hashtags,
        track="drama",
        destination=DRAMA_DESTINATION,
        story_id=story_id,
    )

    base_dir = os.path.join(config.VIDEO_OUTPUT_DIR, "drama", date.today().isoformat())
    os.makedirs(base_dir, exist_ok=True)

    # TTS với voice riêng của track drama (Phase 4).
    from video.tts_client import synthesize_for_track, get_audio_duration
    from video.text_preprocessor import preprocess_for_tts
    audio_path = os.path.join(base_dir, f"audio_{video_id}.mp3")
    if not synthesize_for_track(preprocess_for_tts(narration), "drama", audio_path):
        _fail(video_id, f"TTS failed for story {story_id}")
        return None
    duration = get_audio_duration(audio_path)
    if duration <= 0:
        _fail(video_id, f"Cannot determine audio duration for story {story_id}")
        return None

    # Subtitle: whisper-aligned nếu bật, fallback word-count (same as track AI).
    from video.subtitle_generator import generate_srt, write_entries_srt
    srt_path = os.path.join(base_dir, f"subtitle_{video_id}.srt")
    entries = None
    if config.SUBTITLE_TIMING_MODE == "whisper":
        try:
            from video.subtitle_aligner import align
            entries = align(audio_path, narration)
        except Exception as e:
            logger.warning("Whisper alignment error: %s — using word-count", e)
    result = (write_entries_srt(entries, srt_path) if entries
              else generate_srt(narration, duration, srt_path))
    if not result:
        _fail(video_id, f"Subtitle generation failed for story {story_id}")
        return None

    # Compose multi-scene (Phase 4). Drama là video short — burn theo policy.
    burn = config.should_burn_subtitles("short")
    from video.drama_composer import compose_drama_video
    video_path = os.path.join(base_dir, f"video_{video_id}.mp4")
    if not compose_drama_video(
        audio_path, srt_path if burn else None, video_path,
        thumbnail_prompt=rewrite.get("thumbnail_prompt"),
        vn_commentary=rewrite.get("vn_commentary"),
    ):
        _fail(video_id, f"Composition failed for story {story_id}")
        return None

    update_video_paths(video_id, audio_path=audio_path, subtitle_path=srt_path,
                       video_path=video_path)
    set_video_subtitles_burned(video_id, burn)

    # Thumbnail (best-effort): tái dùng illustration đã cache cho scene đầu —
    # cùng (prompt, index=0) nên thường là cache hit, không tốn thêm API call.
    try:
        from video.image_generator import generate_illustration
        thumb = generate_illustration(rewrite.get("thumbnail_prompt", ""), index=0)
        if thumb:
            update_video_metadata(video_id, thumbnail_path=thumb)
    except Exception as e:
        logger.warning("Thumbnail generation failed (non-fatal): %s", e)

    update_video_status(video_id, "ready")
    update_status(story_id, "produced",
                  produced_at=datetime.now().isoformat(sep=" ", timespec="seconds"))

    from notifier.review_bot import push_review
    if not push_review(video_id):
        logger.error("Could not push video %d for review — video stays 'ready'; "
                     "_repush_stuck_reviews() sẽ tự gửi lại ở lần chạy render sau",
                     video_id)

    logger.info("Story %d rendered → video %d (%.0fs audio)", story_id, video_id, duration)
    return video_id


def run_daily(steps: list[str] | None = None, limit: int | None = None) -> dict:
    """Chạy pipeline Drama. `steps` None = đủ 4 bước. Returns summary dict."""
    logger.info("=== Drama pipeline started ===")
    init_db()
    steps = steps or ["collect", "score", "rewrite", "render"]
    summary: dict = {"errors": []}

    if "collect" in steps:
        collected = 0
        # Reddit (off by default, issue #78) + Lemmy (open Reddit-alternative).
        # Each source is independent: one failing doesn't sink the other or the
        # rest of the pipeline. HuggingFace bulk import is a separate manual tool
        # (collectors/hf_drama_importer.py), not part of the daily run.
        for name, collector in (("reddit", "collectors.reddit_drama_collector"),
                                ("lemmy", "collectors.lemmy_drama_collector")):
            try:
                mod = __import__(collector, fromlist=["*"])
                fn = getattr(mod, "collect_all_drama", None) or getattr(mod, "collect_all_lemmy")
                collected += fn()
            except Exception as e:
                logger.error("Collect (%s) failed: %s", name, e)
                summary["errors"].append(f"collect[{name}]: {e}")
        summary["collected"] = collected

    if "score" in steps:
        try:
            from processors.drama_scorer import score_all_pending
            summary["scored"] = score_all_pending()
        except Exception as e:
            logger.error("Score failed: %s", e)
            summary["errors"].append(f"score: {e}")

    if "rewrite" in steps:
        try:
            from processors.drama_rewriter import rewrite_all_scored
            summary["rewritten"] = rewrite_all_scored()
        except Exception as e:
            logger.error("Rewrite failed: %s", e)
            summary["errors"].append(f"rewrite: {e}")

    if "render" in steps:
        try:
            summary["rendered"] = render_approved_stories(limit=limit)
        except Exception as e:
            logger.error("Render failed: %s", e)
            summary["errors"].append(f"render: {e}")

    _send_summary_safe(summary)
    logger.info("=== Drama pipeline completed: %s ===", summary)
    return summary


def _send_summary_safe(summary: dict) -> None:
    try:
        from notifier.telegram_bot import send_alert
        lines = [f"🎭 DRAMA PIPELINE — {date.today().strftime('%d/%m/%Y')}"]
        if "collected" in summary:
            lines.append(f"📥 Thu thập: {summary['collected']} story mới")
        if "scored" in summary:
            lines.append(f"🎯 Chấm điểm: {summary['scored']} story")
        if "rewritten" in summary:
            lines.append(f"✍️ Việt hoá: {summary['rewritten']} story")
        if "rendered" in summary:
            n = len(summary["rendered"])
            lines.append(f"🎬 Render: {n} video" +
                         (" (đã gửi duyệt — kiểm tra Telegram)" if n else ""))
        if summary["errors"]:
            lines.append(f"⚠️ Lỗi ({len(summary['errors'])}):")
            lines += [f"  • {e}" for e in summary["errors"][:5]]
        send_alert("\n".join(lines))
    except Exception as e:
        logger.warning("Summary notification failed (non-fatal): %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drama track orchestrator (Phase 5)")
    parser.add_argument("--step", choices=["collect", "score", "rewrite", "render"],
                        action="append", dest="steps",
                        help="Chạy riêng 1 bước (lặp lại flag để chạy nhiều bước); "
                             "bỏ trống = chạy đủ pipeline")
    parser.add_argument("--limit", type=int, default=None,
                        help=f"Số video render tối đa (mặc định "
                             f"DRAMA_VIDEOS_PER_RUN={config.DRAMA_VIDEOS_PER_RUN})")
    args = parser.parse_args()
    run_daily(steps=args.steps, limit=args.limit)
