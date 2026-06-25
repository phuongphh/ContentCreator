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
    get_video, update_video_status, get_videos_by_status,
)

logger = logging.getLogger(__name__)


def list_pending() -> list[dict]:
    """Return videos awaiting approval."""
    return get_videos_by_status("pending_approval")


def approve(video_id: int, publish_callback=None) -> tuple[bool, str]:
    """Approve a pending video and (optionally) trigger publishing.

    Returns (ok, message). Guards against approving a video that is missing or
    not in the pending_approval state, so a double-approve can't republish.
    """
    video = get_video(video_id)
    if not video:
        return False, f"Video {video_id} không tồn tại."
    if video.get("status") != "pending_approval":
        return False, (f"Video {video_id} không ở trạng thái chờ duyệt "
                       f"(status={video.get('status')}).")

    update_video_status(video_id, "approved")
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
