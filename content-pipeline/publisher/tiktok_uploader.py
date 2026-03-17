"""
TikTok Uploader — Upload video lên TikTok qua Content Publishing API.

Yêu cầu:
- TikTok Developer Account với Content Publishing API access
- Access token (OAuth2)

Flow:
1. Init upload → nhận upload_url
2. Upload video file lên upload_url
3. Publish video với caption và hashtags
"""

import json
import logging
import os
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


def upload_video(video_path: str, caption: str, hashtags: str = "") -> str | None:
    """Upload a video to TikTok.

    Args:
        video_path: Path to the MP4 file.
        caption: Video caption.
        hashtags: Space-separated hashtags string.

    Returns:
        TikTok publish_id, or None on failure.
    """
    if not config.TIKTOK_ACCESS_TOKEN:
        logger.error("TIKTOK_ACCESS_TOKEN not configured")
        return None

    if not os.path.exists(video_path):
        logger.error("Video file not found: %s", video_path)
        return None

    # Combine caption and hashtags
    full_caption = caption
    if hashtags:
        full_caption = f"{caption}\n\n{hashtags}"

    video_size = os.path.getsize(video_path)

    # Step 1: Initialize upload
    init_url = f"{TIKTOK_API_BASE}/post/publish/video/init/"
    init_payload = json.dumps({
        "post_info": {
            "title": full_caption[:150],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1,
        },
    }).encode("utf-8")

    try:
        req = Request(
            init_url,
            data=init_payload,
            headers={
                "Authorization": f"Bearer {config.TIKTOK_ACCESS_TOKEN}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        with urlopen(req, timeout=30) as resp:
            init_data = json.loads(resp.read().decode())

        if init_data.get("error", {}).get("code") != "ok":
            logger.error("TikTok init failed: %s", init_data)
            return None

        upload_url = init_data["data"]["upload_url"]
        publish_id = init_data["data"]["publish_id"]

    except Exception as e:
        logger.error("TikTok init upload failed: %s", e)
        return None

    # Step 2: Upload video file
    try:
        with open(video_path, "rb") as f:
            video_data = f.read()

        req = Request(
            upload_url,
            data=video_data,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
            },
            method="PUT",
        )
        with urlopen(req, timeout=300) as resp:
            if resp.status not in (200, 201):
                logger.error("TikTok upload returned %d", resp.status)
                return None

    except Exception as e:
        logger.error("TikTok video upload failed: %s", e)
        return None

    logger.info("TikTok upload complete: publish_id=%s", publish_id)
    return publish_id


def check_publish_status(publish_id: str) -> dict | None:
    """Check the status of a published video."""
    if not config.TIKTOK_ACCESS_TOKEN:
        return None

    url = f"{TIKTOK_API_BASE}/post/publish/status/fetch/"
    payload = json.dumps({"publish_id": publish_id}).encode("utf-8")

    try:
        req = Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {config.TIKTOK_ACCESS_TOKEN}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get("data", {})
    except Exception as e:
        logger.error("TikTok status check failed: %s", e)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("TikTok uploader ready.")
    print("Configure TIKTOK_ACCESS_TOKEN in .env")
