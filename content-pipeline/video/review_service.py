from __future__ import annotations

"""
Review Service (P2) — single source of truth for approve/reject of videos.

Shared by the Telegram bot and the optional Web UI so both go through identical
state transitions and the same publish path. Pure DB/state logic — no network,
no Telegram, no Streamlit — so it is easy to unit-test.
"""

import logging

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.database import (
    get_video, update_video_status, get_videos_by_status, claim_video_status,
)

logger = logging.getLogger(__name__)


def list_pending() -> list[dict]:
    """Return videos awaiting approval."""
    return get_videos_by_status("pending_approval")


def approve(video_id: int, publish_callback=None) -> tuple[bool, str]:
    """Approve a pending video and (optionally) trigger publishing.

    Returns (ok, message). The pending→approved transition is performed as an
    atomic conditional update so that concurrent approvals (Telegram + Web UI)
    cannot both claim the same video and publish it twice — only the caller that
    actually flips the row proceeds to publish.
    """
    video = get_video(video_id)
    if not video:
        return False, f"Video {video_id} không tồn tại."

    claimed = claim_video_status(video_id, "approved", "pending_approval")
    if not claimed:
        # Either already handled, or another reviewer just claimed it.
        current = get_video(video_id)
        status = current.get("status") if current else "?"
        return False, (f"Video {video_id} không ở trạng thái chờ duyệt "
                       f"(status={status}).")

    logger.info("Video %d approved via review_service", video_id)
    if publish_callback is not None:
        publish_callback(video_id)
    return True, f"Video {video_id} đã duyệt."


def reject(video_id: int) -> tuple[bool, str]:
    """Reject a video. Returns (ok, message)."""
    video = get_video(video_id)
    if not video:
        return False, f"Video {video_id} không tồn tại."
    update_video_status(video_id, "rejected")
    logger.info("Video %d rejected via review_service", video_id)
    return True, f"Video {video_id} đã bị từ chối."
