from __future__ import annotations

"""
Telegram Bot — Persistent bot chạy long-polling.

Khi nhận /approve → publish ngay lập tức, không cần cronjob.

Chạy: python main.py --bot (chạy liên tục như daemon)
"""

import json
import logging
import os
import signal
import socket
import threading
import time
from datetime import date
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import (
    get_video, update_video_status, update_video_telegram_id,
    update_video_publish_url, get_videos_by_status,
)

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096
# Telegram Bot API caps file uploads at 50 MB. Beyond this, sendVideo is
# guaranteed to fail (Broken pipe / 413), so we skip the doomed upload instead
# of reading the whole file into memory and blocking ~120s on a dead request.
TELEGRAM_MAX_FILE_BYTES = 50 * 1024 * 1024

# Store last update_id to avoid re-processing
_OFFSET_FILE = os.path.join(os.path.dirname(__file__), ".telegram_offset")
# PID lock file — prevents duplicate bot instances causing 409 Conflict
_BOT_LOCK_FILE = os.path.join(os.path.dirname(__file__), ".bot.pid")

# 409 Conflict liên tiếp: deleteWebhook chỉ chữa được nguyên nhân "webhook tồn
# đọng" (root cause #88). Nếu 409 vẫn tiếp diễn thì nguyên nhân là một instance
# getUpdates KHÁC đang chạy — gọi lại deleteWebhook + poll mỗi 5s suốt ngày chỉ
# nã API và làm chính token dễ ăn 429 hơn (issue #119), nên chỉ tự chữa vài lần
# rồi lùi dần tới trần.
_CONFLICT_HEAL_ATTEMPTS = 3
_CONFLICT_BACKOFF_MAX = 60
_conflict_streak = 0


# --- Public API ---

def send_video_for_approval(video_id: int) -> bool:
    """Send a video and its script to Telegram for manual approval.

    The script text is the actual narration used in the video — this is the
    single source of truth that the reviewer must cross-check against the video.
    No separate summary is submitted; the script IS the review artifact.
    """
    video = get_video(video_id)
    if not video:
        logger.error("Video %d not found", video_id)
        return False

    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping approval.")
        return False

    video_path = video.get("video_path")
    if not video_path or not os.path.exists(video_path):
        logger.error("Video file not found: %s", video_path)
        return False

    today = date.today().strftime("%d/%m/%Y")
    vtype = "DÀI (YouTube)" if video["video_type"] == "long" else "NGẮN (Shorts/TikTok)"
    platform = video.get("scheduled_platform", "")
    title = video.get("youtube_title", "") or video.get("tiktok_caption", "")
    # Let the reviewer know to check music levels/licensing when BGM is on.
    bgm_note = "\n🎵 Có nhạc nền — kiểm tra âm lượng & bản quyền." if getattr(
        config, "ENABLE_BGM", False) else ""

    # --- Step 1: Send the script text as the review artifact ---
    # The script IS the primary review artifact, so its successful delivery is
    # what makes the video reviewable — NOT the (size-limited, flaky) video
    # upload below. We capture the send result to drive the status transition.
    script_sent = False
    script_text = video.get("script_text", "")
    if script_text:
        word_count = len(script_text.split())
        script_header = (
            f"📋 SCRIPT VIDEO #{video_id} — ĐỌC VÀ DUYỆT\n"
            f"{'=' * 30}\n"
            f"📌 Loại: {vtype}\n"
            f"📝 Tiêu đề: {title}\n"
            f"📊 Độ dài: {word_count} từ\n"
            f"{'=' * 30}\n\n"
            f"⚠️ Đây là SCRIPT THẬT dùng trong video.\n"
            f"Hãy đọc kỹ và đối chiếu với video bên dưới.\n\n"
            f"{'─' * 30}\n\n"
            f"{script_text}\n\n"
            f"{'─' * 30}\n"
            f"📌 Xem video bên dưới rồi trả lời:\n"
            f"  ✅ /approve_{video_id}\n"
            f"  ❌ /reject_{video_id}"
        )
        script_sent = _send_text(script_header)
        if script_sent:
            logger.info("Script text sent for review (video %d, %d words)", video_id, word_count)
        else:
            logger.error("Failed to send script text for video %d", video_id)
    else:
        logger.warning("Video %d has no script_text — sending video only", video_id)

    # --- Step 2: Send the video file ---
    caption = (
        f"🎬 VIDEO CHỜ DUYỆT — {today}\n\n"
        f"📌 Loại: {vtype}\n"
        f"📅 Lịch đăng: {video.get('scheduled_date', 'N/A')} → {platform}\n"
        f"📝 Tiêu đề: {title}{bgm_note}\n\n"
        f"⚠️ Đối chiếu video này với script ở trên.\n"
        f"Script phải khớp 100% với nội dung video.\n\n"
        f"💬 Trả lời:\n"
        f"  ✅ /approve_{video_id}\n"
        f"  ❌ /reject_{video_id}"
    )

    # Video >50MB: nén 1 bản preview riêng (Phase 5 EPIC #5.1) thay vì bỏ qua
    # luôn như trước — reviewer được xem hình thật thay vì chỉ script. Nén
    # thất bại → preview_path=None → giữ nguyên fallback script-only cũ.
    from video.preview import compress_for_preview
    preview_path = compress_for_preview(video_path)
    if preview_path and preview_path != video_path:
        caption += "\nℹ️ Bản preview đã nén — file gốc dùng để upload."

    msg_id = _send_video_file(preview_path, caption) if preview_path else None
    if msg_id:
        update_video_telegram_id(video_id, str(msg_id))

    # --- Step 3: Set status based on whether the video is actually reviewable ---
    # The reviewer can act as long as they received the script OR the video.
    # Decoupling the status from the video upload fixes issue #60: a too-large
    # or broken-pipe video upload must NOT strand the video at status=ready,
    # which would make /approve_<id> fail with "không ở trạng thái chờ duyệt".
    reviewable = script_sent or bool(msg_id)
    if reviewable:
        update_video_status(video_id, "pending_approval")
        if not msg_id:
            # Script reached the reviewer but the video file did not. Tell them
            # explicitly so they don't wait for a video that will never arrive,
            # and remind them they can still act on the script alone.
            _send_text(
                f"⚠️ Không gửi được FILE VIDEO #{video_id} qua Telegram "
                f"(quá lớn >50MB hoặc lỗi mạng).\n"
                f"Script ở trên là bản duyệt chính — bạn vẫn có thể duyệt:\n"
                f"  ✅ /approve_{video_id}\n"
                f"  ❌ /reject_{video_id}"
            )
        logger.info(
            "Video %d set to pending_approval (video_file_sent=%s)", video_id, bool(msg_id)
        )
        return True

    # Neither the script nor the video reached the reviewer. Leave the status at
    # 'ready' so the run can be retried, and report failure upstream.
    logger.error(
        "Video %d: neither script nor video could be delivered — staying 'ready'", video_id
    )
    return False


def send_tiktok_manual(video_id: int) -> bool:
    """Gửi NARRATIVE (script_text) + video đã render tới kênh Bé MC để UPLOAD
    TIKTOK THỦ CÔNG.

    Mô hình TikTok mới: pipeline không auto-upload; nó đẩy narrative text
    (chính narration đọc trong video — để Bé MC làm caption/mô tả/đối chiếu)
    rồi video + caption/hashtags vào kênh Bé MC (config.TELEGRAM_TIKTOK_CHAT_ID,
    fallback TELEGRAM_CHAT_ID) và dừng — Bé MC tự tải lên TikTok. Áp dụng cho
    MỌI video TikTok, cả track AI lẫn Drama (cả 2 đều route qua đây).

    Gửi FILE GỐC (giữ nguyên chất lượng để upload). Nếu >50MB (trần Telegram
    bot) hoặc gửi lỗi → export ra queue tay local (publisher/tiktok_manual) rồi
    gửi 1 tin nhắn báo đường dẫn file gốc, để Bé MC vẫn upload được bản nét.

    TELEGRAM_TIKTOK_CHAT_ID nhận NHIỀU chat id cách nhau dấu phẩy — mỗi người
    nhận trọn bộ narrative + video (issue #107 follow-up). Trả True nếu video
    HOẶC thông báo fallback đã tới ÍT NHẤT một người nhận (người lỗi chỉ log
    warning, không kéo sập cả lượt gửi).
    """
    video = get_video(video_id)
    if not video:
        logger.error("send_tiktok_manual: video %d not found", video_id)
        return False
    chat_ids = _tiktok_chat_ids()
    if not config.TELEGRAM_BOT_TOKEN or not chat_ids:
        logger.warning("Bé MC chat chưa cấu hình — bỏ qua gửi TikTok video %d", video_id)
        return False
    video_path = video.get("video_path")
    if not video_path or not os.path.exists(video_path):
        logger.error("send_tiktok_manual: file missing for video %d: %s",
                     video_id, video_path)
        return False

    title = video.get("youtube_title", "") or video.get("tiktok_caption", "")
    tiktok_caption = video.get("tiktok_caption", "")
    hashtags = video.get("tiktok_hashtags", "")
    narration = video.get("script_text", "") or ""

    # File gốc trong ngưỡng → gửi thẳng (chất lượng nguyên vẹn cho upload).
    try:
        size_ok = os.path.getsize(video_path) <= TELEGRAM_MAX_FILE_BYTES
    except OSError:
        size_ok = False

    # Export queue tay chỉ chạy 1 LẦN cho cả lượt (file chung, không phụ thuộc
    # người nhận) và chỉ khi thật sự cần fallback — None = chưa export.
    exported_note: str | None = None
    # file_id Telegram của lần upload đầu tiên thành công trong lượt này —
    # người nhận sau gửi lại theo file_id, không upload lại file.
    cached_file_id: str | None = None

    def _fallback_note() -> str:
        nonlocal exported_note
        if exported_note is None:
            exported_note = ""
            try:
                from publisher.tiktok_manual import export_for_manual_upload
                exported = export_for_manual_upload(video_id)
                if exported:
                    exported_note = f"\n📁 File gốc (nét) đã lưu: {exported}"
            except Exception as e:
                logger.warning("Export queue tay cho video %d lỗi (non-fatal): %s",
                               video_id, e)
        return exported_note

    delivered_any = False
    for chat_id in chat_ids:
        # Narrative (script_text) đi TRƯỚC video — yêu cầu chủ kênh: Bé MC nhận
        # cả text lẫn video cho MỌI video TikTok (cả track AI lẫn Drama đều
        # route qua hàm này). script_text là chính narration đọc trong video
        # (một nguồn text cho TTS/phụ đề/review), nên Bé MC dùng nó làm
        # caption/mô tả hoặc đối chiếu nội dung mà không phải chờ hỏi lại.
        # Best-effort: text lỗi không chặn gửi video.
        narrative_sent = False
        if narration.strip():
            narrative_sent = _send_text_chunks(
                f"📋 NARRATIVE VIDEO TIKTOK #{video_id} ({len(narration.split())} từ)\n"
                f"{'─' * 30}\n\n{narration}",
                chat_id=chat_id,
            )
            if not narrative_sent:
                logger.warning("Không gửi được narrative video %d tới chat %s "
                               "(vẫn gửi video)", video_id, chat_id)

        caption = (
            f"🎵 VIDEO TIKTOK #{video_id} — UPLOAD TAY\n"
            f"📝 {title}\n"
            + (f"💬 Caption: {tiktok_caption}\n" if tiktok_caption else "")
            + (f"🏷 {hashtags}\n" if hashtags else "")
            + ("📋 Narrative (script) ở tin nhắn phía trên.\n" if narrative_sent else "")
            + "➡️ Tải video này lên TikTok giúp nhé (Bé MC tự đăng)."
        )

        sent = False
        if size_ok:
            # Người nhận thứ 2+ tái dùng file_id của lần upload đầu (gửi tức
            # thì, không re-upload ~50MB); file_id lỗi → rơi về upload thường.
            msg_id = None
            if cached_file_id:
                msg_id = _send_video_by_file_id(cached_file_id, caption, chat_id)
            if not msg_id:
                msg_id = _send_video_file(video_path, caption, chat_id=chat_id)
                if msg_id:
                    cached_file_id = _last_video_file_id
            if msg_id:
                logger.info("Video %d gửi tới chat %s để upload TikTok tay",
                            video_id, chat_id)
                sent = True
            else:
                logger.warning("Gửi video %d tới chat %s lỗi — chuyển sang "
                               "fallback queue tay", video_id, chat_id)

        if not sent:
            # >50MB hoặc gửi lỗi → giữ bản gốc trong queue tay + báo đường dẫn.
            sent = _send_single_text(
                caption + "\n\n⚠️ File quá lớn để gửi qua Telegram." + _fallback_note(),
                chat_id=chat_id,
            )
        delivered_any = delivered_any or sent

    return delivered_any


def send_publish_notification(video_id: int, platform: str, url: str) -> bool:
    """Notify via Telegram that a video has been published.

    Trả False khi KHÔNG gửi được (vd rate-limit dai dẳng) để caller ghi rõ
    "video đã lên sóng nhưng không báo được" vào log — tình huống của issue
    #119, trước đây chỉ để lại một dòng "Telegram send failed" trơ trọi.
    """
    return _send_text(f"🚀 Video {video_id} đã đăng lên {platform}!\n🔗 {url}")


def send_alert(text: str) -> bool:
    """Send an arbitrary alert/notification message.

    Public wrapper around the internal `_send_text` for callers outside this
    module (e.g. storage/collector_health.py's stale-collector check) that
    just need to push a plain text message, without reaching into a
    leading-underscore "private" helper.
    """
    return _send_text(text)


def send_narrative_report(narrative: str, article_count: int) -> bool:
    """Send the narrative summary as a text message before video generation.

    This ensures the user always receives the daily summary even if
    video generation fails downstream.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping narrative.")
        return False

    today = date.today().strftime("%d/%m/%Y")
    header = (
        f"📝 TÓM TẮT AI HÔM NAY — {today}\n"
        f"({article_count} bài đã phân tích)\n\n"
        f"ℹ️ Đây là bản tóm tắt tham khảo — KHÔNG phải script video.\n"
        f"Script thật sẽ được gửi kèm video để duyệt.\n\n"
    )
    full_text = header + narrative

    # Telegram max is 4096 chars — split if needed
    if len(full_text) <= TELEGRAM_MAX_LENGTH:
        return _send_text(full_text)

    # Split into chunks at paragraph boundaries
    parts = _split_message(full_text)
    success = True
    for part in parts:
        if not _send_text(part):
            success = False
    return success


def _split_message(text: str, max_len: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split long text into chunks at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Find last double-newline within limit
        split_at = text.rfind("\n\n", 0, max_len)
        if split_at == -1:
            # Fall back to single newline
            split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            # Last resort: hard cut
            split_at = max_len

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks


def send_pipeline_summary(long_count: int, short_count: int, errors: list[str]):
    """Send a summary of the pipeline run."""
    today = date.today().strftime("%d/%m/%Y")
    lines = [f"📊 PIPELINE SUMMARY — {today}\n"]

    if long_count > 0:
        lines.append(f"🎬 Video dài: {long_count} video đã tạo")
    if short_count > 0:
        lines.append(f"📱 Video ngắn: {short_count} video đã tạo")
    if not long_count and not short_count:
        lines.append("⚠️ Không tạo được video nào hôm nay")

    pending = get_videos_by_status("pending_approval")
    if pending:
        lines.append(f"\n⏳ Đang chờ duyệt: {len(pending)} video")

    if errors:
        lines.append(f"\n⚠️ Lỗi ({len(errors)}):")
        for err in errors[:5]:
            lines.append(f"  • {err}")

    _send_text("\n".join(lines))


def _acquire_bot_lock() -> bool:
    """Write current PID to lock file. Return False if another instance is running."""
    if os.path.exists(_BOT_LOCK_FILE):
        try:
            with open(_BOT_LOCK_FILE) as f:
                existing_pid = int(f.read().strip())
            # Check if that PID is still alive
            try:
                os.kill(existing_pid, 0)  # signal 0 = existence check
                logger.warning(
                    "Another bot instance (PID %d) is already running — exiting to prevent 409 Conflict",
                    existing_pid,
                )
                return False
            except (ProcessLookupError, PermissionError):
                # PID not found or not ours — stale lock, overwrite it
                logger.info("Stale bot lock (PID %d) — overwriting", existing_pid)
        except (ValueError, OSError):
            pass  # Malformed or unreadable lock file — overwrite

    try:
        with open(_BOT_LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        logger.warning("Could not write bot lock file: %s", e)
    return True


def _release_bot_lock():
    """Remove PID lock file on clean shutdown."""
    try:
        if os.path.exists(_BOT_LOCK_FILE):
            os.remove(_BOT_LOCK_FILE)
    except OSError:
        pass


# --- Watchdog chống treo (issue #107) ---
# Root cause #107: getUpdates kẹt VĨNH VIỄN ở sock_connect sau chu kỳ ngủ/dậy
# của Mac dù urlopen có timeout=35s — CPython đặt deadline theo đồng hồ
# MONOTONIC (mach_absolute_time trên macOS NGỪNG chạy khi máy ngủ) nên deadline
# không bao giờ tới. PID còn sống (CPU 0%) → launchd KeepAlive không cứu. Lớp
# bảo vệ ngoài: thread watchdog đo pha hiện tại của vòng lặp bằng WALL CLOCK
# (time.time vẫn chạy khi máy ngủ); pha vượt trần → os._exit để launchd restart
# bot sạch (offset getUpdates + PID lock đều được xử lý qua restart). Hệ quả
# phụ CÓ CHỦ ĐÍCH: Mac ngủ dài giữa lúc poll → watchdog restart bot ngay khi
# dậy — chính là trạng thái sạch ta muốn sau sleep.
_WATCHDOG_CHECK_INTERVAL = 15  # giây giữa 2 lần kiểm tra
_watchdog_state = {"phase": "idle", "since": 0.0}


def _watchdog_mark(phase: str) -> None:
    """Ghi pha hiện tại của vòng lặp bot ('poll' | 'handle') + mốc wall-clock."""
    _watchdog_state["phase"] = phase
    _watchdog_state["since"] = time.time()


def _watchdog_verdict(state: dict, now: float) -> str | None:
    """Trả lý do cần kill nếu pha hiện tại vượt trần, None nếu khoẻ.

    Tách thuần để test được không cần thread/không cần chờ thật. Trần theo pha:
    poll (mạng, bình thường ≤40s) chặt hơn hẳn handle (approve → upload có thể
    vài phút). Trần ≤0 = tắt kiểm tra pha đó.
    """
    limits = {
        "poll": getattr(config, "BOT_WATCHDOG_POLL_TIMEOUT", 180),
        "handle": getattr(config, "BOT_WATCHDOG_HANDLE_TIMEOUT", 1800),
    }
    limit = limits.get(state.get("phase", ""))
    if not limit or limit <= 0:
        return None
    elapsed = now - state.get("since", now)
    if elapsed > limit:
        return (f"pha '{state['phase']}' kẹt {elapsed:.0f}s "
                f"(trần {limit}s)")
    return None


def _watchdog_loop() -> None:
    while True:
        time.sleep(_WATCHDOG_CHECK_INTERVAL)
        reason = _watchdog_verdict(_watchdog_state, time.time())
        if reason:
            logger.critical(
                "Watchdog: %s — os._exit để launchd KeepAlive restart bot "
                "(socket timeout không tin được qua sleep/wake, issue #107)",
                reason,
            )
            _release_bot_lock()
            # os._exit (không phải sys.exit): main thread đang kẹt trong
            # syscall, chỉ hạ cả process mới chắc chắn thoát. 70 = EX_SOFTWARE
            # (KHÔNG dùng 78/EX_CONFIG — launchd khoá job exit 78 tới khi reload).
            os._exit(70)


def _start_watchdog() -> threading.Thread:
    t = threading.Thread(target=_watchdog_loop, name="bot-watchdog", daemon=True)
    t.start()
    return t


def _sigterm_handler(signum, frame):
    """launchd gửi SIGTERM khi Mac ngủ/reload/shutdown (issue #107: 'exit -15').

    Raise SystemExit để unwind stack — signal làm syscall đang chờ (kể cả
    sock_connect kẹt) trả EINTR nên bot thoát được ngay; finally trong run_bot
    nhả PID lock → instance mới không phải chờ stale-lock check.
    """
    raise SystemExit(0)


def run_bot(publish_callback):
    """Run persistent Telegram bot with long-polling.

    Listens for /approve_<id> and /reject_<id> commands.
    On approve → immediately calls publish_callback(video_id) to upload.

    Args:
        publish_callback: function(video_id) -> None, handles publishing.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured — cannot run bot")
        return

    if not _acquire_bot_lock():
        return  # Another instance running — exit cleanly (no 409)

    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except ValueError:
        # signal.signal chỉ gọi được từ main thread — bot nhúng trong thread
        # phụ (test/tool) vẫn chạy, chỉ mất graceful shutdown.
        logger.warning("Không đặt được SIGTERM handler (không phải main thread)")

    # Watchdog phải sống TRƯỚC call mạng đầu tiên: _delete_webhook/_send_text
    # khởi động cũng đi đúng đường sock_connect có thể treo vĩnh viễn sau
    # sleep/wake (review Codex PR #108). Đánh pha "poll" vì các call khởi động
    # đều là network op ngắn (timeout ≤10s) — trần poll 180s bao chúng thoải
    # mái; vòng lặp bên dưới sẽ tự đánh lại pha mỗi iteration.
    _watchdog_mark("poll")
    _start_watchdog()

    # Webhook tồn đọng khiến MỌI getUpdates trả 409 Conflict — xoá 1 lần lúc
    # khởi động để long-polling dùng được (root cause #88). Giữ pending updates
    # để callback approve vừa bấm vẫn tới.
    _delete_webhook()

    logger.info("🤖 Telegram bot started — listening for approvals...")
    _send_text("🤖 Bot đã khởi động. Sẵn sàng nhận lệnh approve/reject.")

    consecutive_errors = 0

    try:
        while True:
            try:
                _watchdog_mark("poll")
                updates = _get_updates(timeout=30)
                _watchdog_mark("handle")
                consecutive_errors = 0  # Reset on success

                for update in updates:
                    _handle_update(update, publish_callback)

            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                _send_text("🛑 Bot đã dừng.")
                break
            except Exception as e:
                # Backoff không phải poll — chuyển pha để trần poll (chặt)
                # không chém nhầm giấc ngủ backoff hợp lệ (tối đa 60s).
                _watchdog_mark("handle")
                consecutive_errors += 1
                wait = min(2 ** consecutive_errors, 60)
                logger.error("Bot error (attempt %d): %s — retrying in %ds",
                             consecutive_errors, e, wait)
                time.sleep(wait)
    except SystemExit:
        # SIGTERM từ launchd (ngủ/reload/shutdown): thoát nhanh, KHÔNG gửi
        # Telegram — mạng có thể đã down và launchd chỉ chờ vài giây trước
        # khi SIGKILL; finally bên dưới vẫn nhả lock.
        logger.info("Bot nhận SIGTERM — thoát gọn")
    finally:
        _release_bot_lock()


def _handle_update(update: dict, publish_callback):
    """Process a single Telegram update."""
    # Review gate (Phase 5): nút inline ✅/❌/✏️ tới dưới dạng callback_query,
    # không phải message.
    if "callback_query" in update:
        _handle_callback_query(update["callback_query"])
        return

    message = update.get("message", {})
    text = message.get("text", "").strip()
    chat_id = str(message.get("chat", {}).get("id", ""))

    if chat_id != config.TELEGRAM_CHAT_ID:
        return

    # File đính kèm (Phase 6): CSV số liệu TikTok. Chỉ tải file khi đang chờ
    # (analytics_bot.is_awaiting_csv) để không tải nhầm mọi document gửi tới.
    document = message.get("document")
    if document:
        from notifier import analytics_bot
        if analytics_bot.is_awaiting_csv():
            content = _download_file(document.get("file_id", ""))
            if content is None:
                _send_text("⚠️ Không tải được file. Thử lại /import_tiktok_csv.")
            else:
                _send_text(analytics_bot.handle_csv_document(
                    content, document.get("file_name", "")))
        return

    # Plain (non-command) messages answer whichever conversation is awaiting
    # input: review gate FSM (reject reason / edit metadata, Phase 5) first,
    # then drama seed bot (/seed_vn, /seed_url — Phase 2). Commands below
    # always take priority even mid-conversation.
    if not text.startswith("/"):
        from notifier import review_bot, seed_bot
        reply = review_bot.handle_awaiting_message(text)
        if reply is None:
            reply = seed_bot.handle_awaiting_message(text)
        if reply is not None:
            _send_text(reply)
        return

    if text == "/skip":
        from notifier import review_bot, analytics_bot
        reply = review_bot.skip_awaiting()
        if reply is None:
            reply = analytics_bot.skip_awaiting()
        _send_text(reply if reply is not None else "✨ Không có câu hỏi nào đang chờ.")
        return

    if text == "/import_tiktok_csv":
        from notifier import analytics_bot
        _send_text(analytics_bot.start_import_tiktok_csv())
        return

    if text.startswith("/approve_"):
        try:
            video_id = int(text.split("_", 1)[1])
        except (ValueError, IndexError):
            _send_text("⚠️ Lệnh không hợp lệ. Dùng: /approve_<số>")
            return
        # Video của review gate (Phase 5, có destination trong channel
        # registry) phải đi qua scheduler routing — kể cả khi reviewer gõ
        # lệnh cũ thay vì bấm nút ✅. publish_video() cũ nhìn
        # scheduled_platform (rỗng với video drama) nên sẽ đăng vào hư không
        # và chặn luôn đường retry vì status đã 'approved'.
        if _is_review_gate_video(video_id):
            from notifier import review_bot
            reply, _ = review_bot.handle_callback(f"rv:a:{video_id}")
            _send_text(reply)
            return
        from video.review_service import approve
        # review_service performs the state transition + publish atomically.
        ok, msg = approve(video_id, publish_callback=publish_callback)
        _send_text(("✅ " if ok else "⚠️ ") + msg + (" Đang upload..." if ok else ""))

    elif text.startswith("/reject_"):
        try:
            video_id = int(text.split("_", 1)[1])
        except (ValueError, IndexError):
            _send_text("⚠️ Lệnh không hợp lệ. Dùng: /reject_<số>")
            return
        if _is_review_gate_video(video_id):
            from notifier import review_bot
            reply, _ = review_bot.handle_callback(f"rv:r:{video_id}")
            _send_text(reply)
            return
        from video.review_service import reject
        ok, msg = reject(video_id)
        _send_text(("❌ " if ok else "⚠️ ") + msg)

    elif text.startswith("/script_"):
        try:
            video_id = int(text.split("_", 1)[1])
            video = get_video(video_id)
            if not video:
                _send_text(f"⚠️ Video {video_id} không tồn tại.")
                return
            script_text = video.get("script_text", "")
            if not script_text:
                _send_text(f"⚠️ Video {video_id} không có script.")
                return
            vtype = "DÀI" if video["video_type"] == "long" else "NGẮN"
            title = video.get("youtube_title", "") or video.get("tiktok_caption", "")
            word_count = len(script_text.split())
            msg = (
                f"📋 SCRIPT VIDEO #{video_id}\n"
                f"📌 Loại: {vtype} | 📊 {word_count} từ\n"
                f"📝 Tiêu đề: {title}\n"
                f"{'─' * 30}\n\n"
                f"{script_text}"
            )
            _send_text(msg)
        except (ValueError, IndexError):
            _send_text("⚠️ Lệnh không hợp lệ. Dùng: /script_<số>")

    elif text == "/status":
        pending = get_videos_by_status("pending_approval")
        if pending:
            lines = ["⏳ Video đang chờ duyệt:"]
            for v in pending:
                title = v.get("youtube_title", "") or v.get("tiktok_caption", "")
                lines.append(f"  • ID {v['id']}: {title} → /script_{v['id']}")
            _send_text("\n".join(lines))
        else:
            _send_text("✨ Không có video nào đang chờ duyệt.")

    elif text == "/seed_vn":
        from notifier import seed_bot
        _send_text(seed_bot.start_seed_vn())

    elif text == "/seed_url":
        from notifier import seed_bot
        _send_text(seed_bot.start_seed_url())

    elif text == "/list_pending":
        from notifier import seed_bot
        _send_text(seed_bot.list_pending_text())

    elif text == "/help":
        from notifier import review_bot, seed_bot, analytics_bot
        _send_text(
            "📖 Lệnh bot:\n"
            "/approve_<id> — Duyệt và đăng video\n"
            "/reject_<id> — Từ chối video\n"
            "/script_<id> — Xem lại script video\n"
            "/status — Xem video đang chờ duyệt\n\n"
            + review_bot.help_text() + "\n\n"
            + seed_bot.help_text() + "\n\n"
            + analytics_bot.help_text()
        )


def _is_review_gate_video(video_id: int) -> bool:
    """Video thuộc flow review gate Phase 5 (route qua scheduler)?

    Phân biệt bằng `destination`: orchestrator mới (main_drama) luôn set
    destination từ channel registry; flow AI legacy để NULL và dùng
    scheduled_platform + publish ngay. Sai khác này giữ 2 flow không giẫm
    chân nhau khi cùng dùng lệnh /approve_<id>.

    Lỗi DB (thiếu bảng/cột trên DB chưa migrate) → False: rơi về flow legacy
    thay vì làm sập cả vòng xử lý update.
    """
    try:
        video = get_video(video_id)
    except Exception as e:
        logger.warning("Cannot check review-gate flag for video %d: %s", video_id, e)
        return False
    return bool(video and video.get("destination"))


def _handle_callback_query(callback_query: dict):
    """Dispatch một lần bấm nút inline (review gate, Phase 5).

    Luôn answerCallbackQuery (kể cả khi xử lý lỗi) để nút hết xoay vòng chờ
    trên client Telegram.
    """
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
    callback_id = callback_query.get("id", "")
    if chat_id != config.TELEGRAM_CHAT_ID:
        _answer_callback_query(callback_id)
        return

    # ACK TRƯỚC, xử lý SAU: approve có thể mất vài giây (ghi DB + xếp lịch +
    # gửi file), lâu hơn cửa sổ answerCallbackQuery của Telegram. Ack ngay để
    # nút nhả tức thì (nút hiện toast "đang xử lý") và tránh 400 "query too old"
    # (root cause #88). Kết quả gửi lại bằng message riêng bên dưới.
    _answer_callback_query(callback_id, "⏳ Đang xử lý…")

    data = callback_query.get("data", "")
    try:
        from notifier import review_bot
        reply, keyboard = review_bot.handle_callback(data)
    except Exception as e:
        logger.exception("Callback handling failed for %r", data)
        reply, keyboard = f"⚠️ Lỗi xử lý: {e}", None

    if keyboard:
        send_message_with_keyboard(reply, keyboard)
    else:
        _send_text(reply)


# --- Internal helpers ---

def _tiktok_chat_ids() -> list[str]:
    """Danh sách chat nhận video TikTok (kênh Bé MC).

    TELEGRAM_TIKTOK_CHAT_ID nhận nhiều id cách nhau dấu phẩy để gửi cùng lúc
    cho nhiều người (issue #107 follow-up); rỗng → fallback TELEGRAM_CHAT_ID
    (không để video rơi vào hư không).
    """
    raw = config.TELEGRAM_TIKTOK_CHAT_ID or config.TELEGRAM_CHAT_ID or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


def _send_video_file(video_path: str, caption: str,
                     reply_markup: dict | None = None,
                     chat_id: str | None = None) -> str | None:
    """Send a video file via Telegram sendVideo API.

    Telegram caption limit is 1024 chars. If caption exceeds this,
    truncate at last newline and send the full caption as a follow-up text.

    `reply_markup`: inline keyboard dict (review gate, Phase 5), attached to
    the video message itself so the buttons sit under the preview.
    `chat_id`: đích gửi (mặc định TELEGRAM_CHAT_ID). Dùng chat_id riêng để gửi
    video TikTok vào kênh Bé MC (config.TELEGRAM_TIKTOK_CHAT_ID).
    """
    chat_id = chat_id or config.TELEGRAM_CHAT_ID

    # Fail fast on oversized files: a >50MB upload is rejected by Telegram after
    # we have already read the whole file into RAM and blocked up to 120s on a
    # request that cannot succeed. Skipping it keeps the caller responsive and
    # lets it fall back to script-only review (issue #60).
    try:
        size = os.path.getsize(video_path)
    except OSError as e:
        logger.error("Cannot stat video file %s: %s", video_path, e)
        return None
    if size > TELEGRAM_MAX_FILE_BYTES:
        logger.error(
            "Video %s is %.1f MB, over Telegram's %d MB bot limit — skipping upload",
            video_path, size / 1024 / 1024, TELEGRAM_MAX_FILE_BYTES // 1024 // 1024,
        )
        return None

    # Telegram caption limit is 1024 chars
    caption_remainder = ""
    if len(caption) > 1024:
        # Truncate at last newline within limit, add continuation marker
        cut_at = caption.rfind("\n", 0, 1000)
        if cut_at == -1:
            cut_at = 1000
        caption_remainder = caption[cut_at:].lstrip("\n")
        caption = caption[:cut_at] + "\n\n⬇️ Xem tiếp bên dưới..."
        logger.info("Caption truncated at 1024 chars, will send remainder as text")

    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
    body_parts = []

    body_parts.append(f"--{boundary}")
    body_parts.append('Content-Disposition: form-data; name="chat_id"')
    body_parts.append("")
    body_parts.append(chat_id)

    body_parts.append(f"--{boundary}")
    body_parts.append('Content-Disposition: form-data; name="caption"')
    body_parts.append("")
    body_parts.append(caption[:1024])

    if reply_markup:
        body_parts.append(f"--{boundary}")
        body_parts.append('Content-Disposition: form-data; name="reply_markup"')
        body_parts.append("")
        body_parts.append(json.dumps(reply_markup))

    text_body = "\r\n".join(body_parts).encode("utf-8")

    filename = os.path.basename(video_path)
    file_header = (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="video"; filename="{filename}"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode("utf-8")

    with open(video_path, "rb") as f:
        file_data = f.read()

    closing = f"\r\n--{boundary}--\r\n".encode("utf-8")
    full_body = text_body + file_header + file_data + closing

    # retry_transient=False: 429 vẫn được thử lại (Telegram từ chối xử lý →
    # không thể tạo tin trùng), nhưng timeout/5xx giữa chừng thì không rõ file
    # ~50MB đã tới hay chưa — gửi lại có nguy cơ Bé MC nhận VIDEO TRÙNG và tốn
    # thêm một lượt upload dài. Mất một tin còn hơn đăng trùng.
    result = _api_call(
        "sendVideo", data=full_body,
        content_type=f"multipart/form-data; boundary={boundary}",
        chat_id=chat_id, timeout=120, retry_transient=False,
    )
    if not result:
        return None
    msg_id = str(result["message_id"])
    # Lưu file_id Telegram cấp cho lần upload này (side channel — KHÔNG đổi
    # return type vì nhiều caller/test dựa vào msg_id). send_tiktok_manual dùng
    # nó để gửi lại cho người nhận tiếp theo mà không phải upload lại cả file
    # (issue #107 follow-up).
    global _last_video_file_id
    _last_video_file_id = (result.get("video") or {}).get("file_id") or None
    # Send remainder of caption as follow-up text if it was truncated
    if caption_remainder:
        _send_text_chunks(f"📝 (tiếp theo)\n\n{caption_remainder}")
    return msg_id


# file_id của lần sendVideo thành công gần nhất (do _send_video_file set).
_last_video_file_id: str | None = None


def _send_video_by_file_id(file_id: str, caption: str, chat_id: str) -> str | None:
    """Gửi lại một video ĐÃ upload bằng file_id Telegram (không re-upload).

    Telegram cho phép truyền file_id thay cho bytes trong sendVideo — gửi cho
    người nhận thứ 2+ gần như tức thì thay vì upload lại file (có thể ~50MB)
    cho từng người. Caption ở đường này luôn ngắn (caption TikTok) nên chỉ cắt
    an toàn ở 1024, không cần logic remainder của _send_video_file.
    """
    if not file_id or not config.TELEGRAM_BOT_TOKEN:
        return None
    result = _api_call(
        "sendVideo",
        {"chat_id": chat_id, "video": file_id, "caption": caption[:1024]},
        chat_id=chat_id, timeout=30,
        # Như _send_video_file: chỉ 429 mới gửi lại. Timeout giữa chừng có thể
        # là "Telegram đã nhận" → gửi lại là Bé MC nhận video TRÙNG.
        retry_transient=False,
        on_http_error=lambda code, desc: logger.warning(
            "sendVideo theo file_id lỗi HTTP %s (fallback re-upload): %s", code, desc),
    )
    return str(result["message_id"]) if result else None


def _send_text_chunks(text: str, chat_id: str | None = None) -> bool:
    """Send a text message via Telegram, splitting into multiple messages if needed.

    Adds [n/total] markers when splitting so user knows the message continues.
    `chat_id` mặc định TELEGRAM_CHAT_ID; truyền chat khác (vd kênh Bé MC) để
    nhắn text dài tới đúng nơi — cùng convention với `_send_single_text`.
    """
    if not config.TELEGRAM_BOT_TOKEN or not (chat_id or config.TELEGRAM_CHAT_ID):
        return False

    # Reserve space for markers like "[2/3]\n" (~10 chars) to avoid overflow
    marker_reserve = 15
    chunks = _split_message(text, max_len=TELEGRAM_MAX_LENGTH - marker_reserve)
    total = len(chunks)
    success = True

    for i, chunk in enumerate(chunks):
        if total > 1:
            chunk = f"[{i + 1}/{total}]\n{chunk}" if i > 0 else f"{chunk}\n\n[1/{total}] ⬇️"
        if not _send_single_text(chunk, chat_id=chat_id):
            success = False

    return success


def _send_text(text: str) -> bool:
    """Send a text message via Telegram.

    Auto-splits into multiple messages if text exceeds 4096 chars.
    """
    return _send_text_chunks(text)


def send_message_with_keyboard(text: str, keyboard: dict) -> bool:
    """Send a text message with an inline keyboard (review gate, Phase 5).

    Text quá 4096 ký tự bị cắt (giữ keyboard) — caller của review gate chỉ
    gửi text ngắn nên thực tế không chạm giới hạn này.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False
    if len(text) > TELEGRAM_MAX_LENGTH:
        text = text[:TELEGRAM_MAX_LENGTH - 20] + "\n\n⚠️ (bị cắt ngắn)"

    return _api_call("sendMessage", {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "reply_markup": keyboard,
    }, chat_id=config.TELEGRAM_CHAT_ID) is not None


# --- Tầng HTTP dùng chung cho MỌI call Telegram (issue #119) ---
#
# Root cause #119: mỗi hàm gửi tự dựng urlopen + `except Exception: log` riêng
# (6 bản sao), KHÔNG bản nào hiểu HTTP 429. Telegram giới hạn ~1 tin/giây tới
# cùng một chat; một cụm tin gửi sát nhau (nhiều chunk text, 2 video lên sóng
# cùng tick, alert từ mấy monitor) đẩy bot vào cửa sổ phạt, và vì không ai đọc
# `retry_after`, mọi tin trong cửa sổ đó bị **mất vĩnh viễn** (12:02 08/09:
# 2 tin "video đã đăng" đều 429, video vẫn lên YouTube nhưng không ai được báo).
#
# Ba lớp sửa, tất cả nằm ở ĐÚNG MỘT chỗ này để không lệch giữa các hàm gửi:
#   1. Pacing chủ động: giãn tối thiểu TELEGRAM_MIN_SEND_INTERVAL giây giữa 2
#      tin tới cùng một chat → không tự đâm vào giới hạn ngay từ đầu.
#   2. Tôn trọng 429: đọc `retry_after` (body `parameters.retry_after`, hoặc
#      header Retry-After), NGỦ đúng ngần ấy rồi GỬI LẠI. 429 = Telegram TỪ
#      CHỐI xử lý, nên gửi lại chắc chắn không tạo tin trùng.
#   3. Cửa sổ phạt dùng chung tiến trình: một 429 làm MỌI call sau đó chờ tới
#      hết hạn thay vì nã tiếp (nã tiếp chỉ khiến Telegram kéo dài hình phạt).
#
# Cố ý KHÔNG đồng bộ cửa sổ phạt giữa các tiến trình (bot, post_scheduler,
# main chạy riêng): trạng thái đó phải nằm ở DB/file chung, thêm ghi đĩa vào
# hot path của mọi tin nhắn. Lớp 2 đã tự đủ cho ca đa tiến trình — mỗi tiến
# trình gặp 429 của mình thì tự lùi đúng khoảng Telegram yêu cầu.
_send_lock = threading.Lock()
_last_send_at: dict[str, float] = {}   # chat_id → mốc monotonic của tin gần nhất
_rate_limited_until = 0.0              # mốc monotonic hết cửa sổ phạt 429


def _redact(text: object) -> str:
    """Che bot token nếu nó lọt vào chuỗi log (URL API chứa token).

    Log của pipeline được dán vào issue/Telegram khi debug — một dòng lỡ mang
    token là mất quyền điều khiển bot. Rẻ, nên áp cho mọi thông điệp lỗi.
    """
    s = str(text)
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "") or ""
    return s.replace(token, "***") if token else s


def _parse_http_error(err: HTTPError) -> tuple[str, float | None]:
    """(mô tả lỗi thật, số giây cần chờ nếu là 429).

    Đọc body ĐÚNG MỘT LẦN: `str(HTTPError)` chỉ cho "HTTP Error 400: Bad
    Request" — lý do thật ("query is too old...", "Too Many Requests: retry
    after 12") nằm trong body, mà body chỉ đọc được một lần (issue #88).
    `retry_after` ưu tiên lấy từ JSON `parameters.retry_after` (Telegram luôn
    gửi kèm), fallback header Retry-After; cắt trần TELEGRAM_RETRY_AFTER_CAP
    để một giá trị điên không treo cron/bot.
    """
    raw = ""
    try:
        raw = err.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    description, retry_after = "", None
    try:
        payload = json.loads(raw)
        description = payload.get("description") or ""
        params = payload.get("parameters") or {}
        if isinstance(params, dict) and params.get("retry_after") is not None:
            retry_after = float(params["retry_after"])
    except Exception:
        pass
    if retry_after is None:
        try:
            header = err.headers.get("Retry-After") if err.headers else None
            retry_after = float(header) if header else None
        except (TypeError, ValueError, AttributeError):
            retry_after = None
    if retry_after is not None:
        cap = float(getattr(config, "TELEGRAM_RETRY_AFTER_CAP", 60))
        retry_after = max(0.0, min(retry_after, cap))
    return _redact(description or raw or str(err)), retry_after


def _read_error_body(err: HTTPError) -> str:
    """Mô tả lỗi thật của một HTTPError (giữ cho các caller chỉ cần text)."""
    return _parse_http_error(err)[0]


def _reserve_send_slot(chat_id: str | None, honor_penalty: bool,
                       budget_left: float) -> float:
    """Giữ chỗ cho lần gửi kế tiếp; trả số giây cần ngủ TRƯỚC khi gửi.

    Tính (và đặt chỗ) trong lock nhưng NGỦ ngoài lock — thread khác vẫn xếp
    hàng đúng thứ tự mà không bị chặn bởi giấc ngủ của thread trước.
    """
    interval = float(getattr(config, "TELEGRAM_MIN_SEND_INTERVAL", 1.0))
    with _send_lock:
        now = time.monotonic()
        wait = 0.0
        if honor_penalty:
            wait = max(wait, _rate_limited_until - now)
        if chat_id and interval > 0:
            wait = max(wait, _last_send_at.get(chat_id, 0.0) + interval - now)
        wait = max(0.0, min(wait, max(0.0, budget_left)))
        if chat_id:
            _last_send_at[chat_id] = now + wait
    return wait


def _mark_sent(chat_id: str | None) -> None:
    """Ghi mốc hoàn tất của một request (Telegram đếm ở thời điểm nhận)."""
    if not chat_id:
        return
    with _send_lock:
        _last_send_at[chat_id] = max(_last_send_at.get(chat_id, 0.0), time.monotonic())


def _note_rate_limit(seconds: float) -> None:
    """Ghi nhận cửa sổ phạt 429 để mọi call sau trong tiến trình cùng lùi."""
    global _rate_limited_until
    with _send_lock:
        _rate_limited_until = max(_rate_limited_until, time.monotonic() + max(0.0, seconds))


def _api_call(method: str, payload: dict | None = None, *,
              params: dict | None = None,
              data: bytes | None = None,
              content_type: str = "application/json",
              chat_id: str | None = None,
              timeout: int = 10,
              retries: int | None = None,
              retry_transient: bool = True,
              honor_penalty: bool = True,
              on_http_error=None):
    """Gọi một method Bot API. Trả `result` của Telegram, None nếu thất bại.

    Args:
        payload: body JSON (tự encode). `data` để tự truyền bytes (multipart).
        params: query string cho call kiểu GET (getUpdates).
        chat_id: đích gửi — CHỈ dùng cho pacing; None = call không gửi tin
            (getUpdates/deleteWebhook) nên không tính vào giới hạn per-chat.
        retries: số lần thử lại (mặc định TELEGRAM_SEND_RETRIES).
        retry_transient: có thử lại khi 5xx/lỗi mạng không. 429 LUÔN được thử
            lại (Telegram từ chối xử lý → gửi lại không thể tạo tin trùng);
            còn timeout giữa chừng thì KHÔNG chắc — nên caller đắt tiền/rủi ro
            trùng (sendVideo ~50MB) tắt cờ này.
        honor_penalty: getUpdates đặt False — nó không phải tin nhắn, chờ theo
            cửa sổ phạt của sendMessage chỉ làm bot điếc.
        on_http_error: fn(code, description) chạy THAY cho log mặc định khi
            call thất bại vì HTTP error (đã hết lượt thử lại) — để caller phân
            loại theo ngữ cảnh của mình (vd 409 của getUpdates, 400 "query is
            too old" của answerCallbackQuery là LÀNH, không phải ERROR).
    """
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    if params:
        url += "?" + urlencode(params)
    if data is None and payload is not None:
        data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": content_type} if data is not None else {}

    retries = int(getattr(config, "TELEGRAM_SEND_RETRIES", 3)) if retries is None else retries
    budget = float(getattr(config, "TELEGRAM_RETRY_BUDGET", 90))
    slept = 0.0
    attempt = 0

    while True:
        wait = _reserve_send_slot(chat_id, honor_penalty, budget - slept)
        if wait > 0:
            time.sleep(wait)
            slept += wait
        try:
            req = Request(url, data=data, headers=headers,
                          method="POST" if data is not None else "GET")
            with urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            _mark_sent(chat_id)
            result = json.loads(body)
            if result.get("ok"):
                return result.get("result")
            # ok:false với HTTP 200 — lỗi nghiệp vụ, thử lại cũng vậy.
            logger.error("Telegram %s failed: %s", method,
                         _redact(result.get("description") or body))
            return None
        except HTTPError as e:
            _mark_sent(chat_id)
            description, retry_after = _parse_http_error(e)
            transient = e.code == 429 or (500 <= e.code < 600 and retry_transient)
            if transient and attempt < retries:
                delay = retry_after if retry_after is not None else min(2 ** attempt, 8)
                if e.code == 429:
                    _note_rate_limit(delay)
                if slept + delay <= budget:
                    logger.warning(
                        "Telegram %s tạm lỗi (HTTP %s) — chờ %.1fs rồi gửi lại "
                        "(lần %d/%d): %s", method, e.code, delay,
                        attempt + 1, retries, description)
                    time.sleep(delay)
                    slept += delay
                    attempt += 1
                    continue
            if e.code == 429:
                _note_rate_limit(retry_after if retry_after is not None else 5)
            if on_http_error is not None:
                on_http_error(e.code, description)
            else:
                logger.error("Telegram %s failed (HTTP %s): %s",
                             method, e.code, description)
            return None
        except Exception as e:
            _mark_sent(chat_id)
            if retry_transient and attempt < retries:
                delay = min(2 ** attempt, 8)
                if slept + delay <= budget:
                    logger.warning("Telegram %s lỗi mạng — chờ %.1fs rồi gửi lại "
                                   "(lần %d/%d): %s", method, delay,
                                   attempt + 1, retries, _redact(e))
                    time.sleep(delay)
                    slept += delay
                    attempt += 1
                    continue
            # Timeout của call hạ tầng (getUpdates long-poll) là nhiễu bình
            # thường — giữ WARNING để không tích rác ERROR trong log (issue
            # #107). Timeout của một call CÓ chat_id nghĩa là tin nhắn của
            # người dùng có thể đã mất → vẫn ERROR.
            timed_out = (isinstance(e, (TimeoutError, socket.timeout))
                         or "timed out" in str(e).lower())
            if timed_out and chat_id is None:
                logger.warning("Telegram %s timeout (transient): %s", method, _redact(e))
            else:
                logger.error("Telegram %s failed: %s", method, _redact(e))
            return None


def _delete_webhook(drop_pending: bool = False) -> bool:
    """Xoá webhook (nếu có) để long-polling không bị 409 Conflict vĩnh viễn.

    Webhook và getUpdates loại trừ nhau: chỉ cần bot còn cấu hình webhook là
    MỌI getUpdates trả 409 bất kể có bao nhiêu instance (root cause #88). Gọi
    1 lần lúc khởi động và tự chữa khi gặp 409. Mặc định KHÔNG drop pending
    updates — callback approve người dùng vừa bấm vẫn cần được nhận.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        return False
    # Không dính cửa sổ phạt của sendMessage: đây là bước TỰ CHỮA 409, hoãn nó
    # lại chỉ kéo dài thời gian bot điếc.
    return _api_call("deleteWebhook", {"drop_pending_updates": drop_pending},
                     honor_penalty=False, retries=1) is not None


def _answer_callback_query(callback_id: str, text: str = "") -> bool:
    """Acknowledge một callback_query để nút inline hết trạng thái loading.

    Phải gọi CÀNG SỚM CÀNG TỐT: Telegram chỉ chấp nhận answerCallbackQuery
    trong ~vài giây kể từ khi callback phát sinh. Nếu ack sau bước xử lý nặng
    (approve = ghi DB + xếp lịch + gửi file) thì callback_id đã hết hạn → 400
    "query is too old". 400 kiểu này là LÀNH (nút đã được bấm, hành động vẫn
    chạy), nên log ở mức info + kèm callback_id để debug thay vì ERROR (issue #88).
    """
    if not callback_id or not config.TELEGRAM_BOT_TOKEN:
        return False
    def _on_error(code: int, description: str) -> None:
        if code == 400:
            logger.info("answerCallbackQuery bỏ qua (id=%s): %s", callback_id, description)
        else:
            logger.error("answerCallbackQuery failed (id=%s, code=%s): %s",
                         callback_id, code, description)

    # retries=0: callback_id chỉ sống vài giây — chờ rồi gửi lại chắc chắn gặp
    # "query is too old", chỉ tổ giữ vòng long-polling. Cũng KHÔNG chờ cửa sổ
    # phạt vì lý do đó; nút đã được ack bằng toast hay chưa không đổi kết quả
    # của hành động đang chạy sau nó.
    return _api_call("answerCallbackQuery",
                     {"callback_query_id": callback_id, "text": text},
                     retries=0, honor_penalty=False,
                     on_http_error=_on_error) is not None


def _send_single_text(text: str, chat_id: str | None = None) -> bool:
    """Send a single text message (must be <= 4096 chars).

    Uses POST with JSON body instead of GET with URL query params,
    because Vietnamese text URL-encoded via quote() can expand 3-6x
    in byte length, exceeding HTTP URL length limits (~8KB).

    `chat_id` mặc định TELEGRAM_CHAT_ID; truyền chat khác để nhắn kênh Bé MC.
    """
    chat_id = chat_id or config.TELEGRAM_CHAT_ID
    if not config.TELEGRAM_BOT_TOKEN or not chat_id:
        return False

    if len(text) > TELEGRAM_MAX_LENGTH:
        logger.warning("Single text message exceeds %d chars (%d), truncating",
                        TELEGRAM_MAX_LENGTH, len(text))
        text = text[:TELEGRAM_MAX_LENGTH - 20] + "\n\n⚠️ (bị cắt ngắn)"

    return _api_call("sendMessage", {"chat_id": chat_id, "text": text},
                     chat_id=chat_id) is not None


def _download_file(file_id: str) -> str | None:
    """Tải nội dung 1 file Telegram về dưới dạng text (getFile → file_path → tải).

    Dùng cho import CSV TikTok (Phase 6). Trả None nếu bất kỳ bước nào lỗi —
    caller báo người dùng thử lại thay vì crash vòng long-polling.
    """
    if not file_id or not config.TELEGRAM_BOT_TOKEN:
        return None
    # Bước tra cứu đi qua _api_call (được 429/backoff bảo vệ như mọi call
    # khác); bước TẢI là file storage, không phải Bot API method, nên giữ
    # urlopen thẳng.
    meta = _api_call("getFile", params={"file_id": file_id},
                     timeout=30, honor_penalty=False)
    if not meta or not meta.get("file_path"):
        return None
    try:
        dl_url = (f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}"
                  f"/{meta['file_path']}")
        with urlopen(Request(dl_url), timeout=60) as resp:
            # utf-8-sig: TikTok Studio CSV hay có BOM ở đầu file.
            return resp.read().decode("utf-8-sig")
    except Exception as e:
        logger.error("Telegram file download failed: %s", _redact(e))
        return None


def _get_updates(timeout: int = 30) -> list[dict]:
    """Get new updates from Telegram using long-polling.

    timeout=30 means Telegram holds the connection open for 30s
    if there are no updates, then returns empty. This is efficient
    and reacts instantly when a message arrives.
    """
    offset = 0
    if os.path.exists(_OFFSET_FILE):
        try:
            with open(_OFFSET_FILE) as f:
                offset = int(f.read().strip()) + 1
        except (ValueError, OSError):
            pass

    def _on_error(code: int, description: str) -> None:
        global _conflict_streak
        if code == 409:
            # 409 = webhook còn sống hoặc instance getUpdates khác đang chạy.
            # deleteWebhook tự chữa nguyên nhân phổ biến (và vô hại nếu do
            # instance khác); ngủ để KHÔNG busy-loop nã API + spam log — trước
            # đây 409 bị nuốt, run_bot reset lỗi về 0 nên poll lại ngay lập tức
            # (root cause #88). 409 DAI DẲNG = instance khác đang giữ kết nối,
            # webhook không phải nguyên nhân → thôi gọi deleteWebhook và lùi
            # dần (5→10→20→…→60s) thay vì nã đều suốt ngày (issue #119).
            _conflict_streak += 1
            wait = min(5 * 2 ** (_conflict_streak - 1), _CONFLICT_BACKOFF_MAX)
            if _conflict_streak <= _CONFLICT_HEAL_ATTEMPTS:
                _delete_webhook()
            logger.warning("getUpdates 409 Conflict (lần %d) — lùi %ds: %s",
                           _conflict_streak, wait, description)
            time.sleep(wait)
        elif code == 429:
            # Long-poll bị rate-limit: _api_call đã tôn trọng retry_after và
            # thử lại; tới đây là hết lượt — vòng lặp run_bot sẽ poll tiếp.
            logger.warning("getUpdates bị rate-limit (429): %s", description)
        else:
            logger.error("Telegram getUpdates failed (code=%s): %s", code, description)

    # retries=0 cho lỗi mạng: vòng lặp run_bot đã có backoff riêng, và một
    # long-poll lỗi chỉ cần poll lại ngay. 429 vẫn được _api_call tự lùi theo
    # retry_after. honor_penalty=False: cửa sổ phạt của sendMessage không được
    # phép làm bot ĐIẾC với lệnh người dùng.
    updates = _api_call(
        "getUpdates", timeout=timeout + 5, retries=0, honor_penalty=False,
        on_http_error=_on_error,
        params={"timeout": timeout, "offset": offset},
    )
    if updates is None:
        return []
    global _conflict_streak
    _conflict_streak = 0    # poll thành công → hết xung đột, trả lại nhịp 5s
    if not updates:
        return []

    last_id = updates[-1]["update_id"]
    try:
        with open(_OFFSET_FILE, "w") as f:
            f.write(str(last_id))
    except OSError as e:
        # Không ghi được offset → update sẽ được xử lý lại sau restart; báo to
        # thay vì im lặng vì đó là nguồn của "bot làm 2 lần cùng một lệnh".
        logger.error("Không ghi được offset getUpdates (%s): %s", _OFFSET_FILE, e)

    return updates


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Use: python main.py --bot")
