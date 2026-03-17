"""
YouTube Uploader — Upload video lên YouTube / YouTube Shorts qua YouTube Data API v3.

Yêu cầu:
- google-api-python-client, google-auth-oauthlib
- OAuth2 client_secret.json (từ Google Cloud Console)
- Lần đầu chạy sẽ mở browser để xác thực, sau đó lưu token.
"""

import json
import logging
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_authenticated_service():
    """Build authenticated YouTube API service."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    token_file = config.YOUTUBE_TOKEN_FILE

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            if not config.YOUTUBE_CLIENT_SECRETS or not os.path.exists(config.YOUTUBE_CLIENT_SECRETS):
                logger.error("YouTube client_secret.json not found: %s", config.YOUTUBE_CLIENT_SECRETS)
                return None
            flow = InstalledAppFlow.from_client_secrets_file(config.YOUTUBE_CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(token_file), exist_ok=True)
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_video(video_path: str, title: str, description: str,
                 is_short: bool = False) -> str | None:
    """Upload a video to YouTube.

    Args:
        video_path: Path to the MP4 file.
        title: Video title.
        description: Video description.
        is_short: If True, adds #Shorts tag for YouTube Shorts.

    Returns:
        YouTube video URL, or None on failure.
    """
    from googleapiclient.http import MediaFileUpload

    service = _get_authenticated_service()
    if not service:
        return None

    if is_short and "#Shorts" not in title:
        title = f"{title} #Shorts"

    tags = ["AI", "AI Việt Nam", "công nghệ", "AI 5 phút"]
    if is_short:
        tags.append("Shorts")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": "28",  # Science & Technology
            "defaultLanguage": "vi",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)

    try:
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload progress: %d%%", int(status.progress() * 100))

        video_id = response["id"]
        url = f"https://youtu.be/{video_id}"
        logger.info("YouTube upload complete: %s", url)
        return url

    except Exception as e:
        logger.error("YouTube upload failed: %s", e)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("YouTube uploader ready.")
    print("Configure YOUTUBE_CLIENT_SECRETS in .env")
    print("Run this module to test OAuth flow.")
