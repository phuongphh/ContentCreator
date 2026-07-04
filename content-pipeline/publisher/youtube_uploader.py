from __future__ import annotations

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

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # Required for captions().insert (uploading a caption/subtitle track).
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def _video_id_from_url(url_or_id: str) -> str:
    """Extract the YouTube video id from a youtu.be/watch URL or raw id."""
    if not url_or_id:
        return ""
    s = url_or_id.strip()
    if "youtu.be/" in s:
        return s.rsplit("youtu.be/", 1)[1].split("?")[0].split("/")[0]
    if "watch?v=" in s:
        return s.split("watch?v=", 1)[1].split("&")[0]
    return s  # already a bare id


def _has_required_scopes(granted) -> bool:
    """True if all scopes in SCOPES were granted to the saved token."""
    return set(SCOPES).issubset(set(granted or []))


def _get_authenticated_service(token_file: str | None = None):
    """Build authenticated YouTube API service.

    `token_file` defaults to config.YOUTUBE_TOKEN_FILE (the single-channel
    behaviour used by upload_video/upload_caption today). Pass an explicit
    path to authenticate a *different* channel using the same OAuth2 client
    — one Google Cloud OAuth client can authorize multiple Google accounts;
    what differs is which token file the resulting credentials are saved to.
    See docs/current/oauth-setup.md and this module's __main__ CLI below.
    """
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    token_file = token_file or config.YOUTUBE_TOKEN_FILE

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        # A token minted before youtube.force-ssl was added only has
        # youtube.upload; refreshing keeps the old scopes, so caption upload
        # would fail. Force a full re-auth when required scopes are missing.
        if creds and not _has_required_scopes(getattr(creds, "scopes", None)):
            logger.warning(
                "Saved YouTube token is missing required scopes (need %s) — "
                "re-authenticating to grant caption-upload permission.", SCOPES,
            )
            creds = None

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


def upload_caption(video_url_or_id: str, srt_path: str,
                   language: str = "vi", name: str = "Tiếng Việt") -> bool:
    """Upload an SRT file as a caption track for an existing YouTube video.

    Lets long videos ship accurate, viewer-toggleable captions (the script text
    we control) instead of burning subtitles into the frame. Returns True on
    success.
    """
    video_id = _video_id_from_url(video_url_or_id)
    if not video_id:
        logger.error("upload_caption: could not resolve video id from %r", video_url_or_id)
        return False
    if not srt_path or not os.path.exists(srt_path):
        logger.error("upload_caption: SRT not found: %s", srt_path)
        return False

    from googleapiclient.http import MediaFileUpload

    service = _get_authenticated_service()
    if not service:
        return False

    try:
        media = MediaFileUpload(srt_path, mimetype="application/octet-stream",
                                resumable=False)
        service.captions().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "language": language,
                    "name": name,
                    "isDraft": False,
                }
            },
            media_body=media,
        ).execute()
        logger.info("Caption track uploaded for video %s (%s)", video_id, language)
        return True
    except Exception as e:
        logger.error("Caption upload failed for %s: %s", video_id, e)
        return False


def _authenticate_and_print(token_file: str | None):
    """Run the OAuth flow (if needed) and print which channel it authenticated
    as — the key check when authorizing a second channel/account so you catch
    a wrong-account mistake before it ever reaches an upload.
    """
    resolved_path = token_file or config.YOUTUBE_TOKEN_FILE
    service = _get_authenticated_service(token_file)
    if not service:
        print("Authentication failed — check YOUTUBE_CLIENT_SECRETS in .env.")
        return
    try:
        resp = service.channels().list(part="snippet", mine=True).execute()
    except Exception as e:
        print(f"Authenticated, but failed to fetch channel info: {e}")
        return
    items = resp.get("items", [])
    if not items:
        print("Authenticated, but no channel found for this account.")
        return
    for item in items:
        print(f"Authenticated as channel: {item['snippet']['title']}")
    print(f"Token saved to: {resolved_path}")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description=(
            "YouTube uploader. Run directly to (re)authenticate a channel and "
            "save its OAuth token — use --token-file to set up a SECOND channel "
            "(e.g. drama_youtube) with the same OAuth2 client from Google Cloud "
            "Console without overwriting the first channel's token. See "
            "docs/current/oauth-setup.md."
        )
    )
    parser.add_argument(
        "--token-file",
        help="Where to save the OAuth token (default: config.YOUTUBE_TOKEN_FILE). "
             "Use a distinct path per channel, e.g. "
             "publisher/.youtube_token_drama.json for the Drama channel.",
    )
    args = parser.parse_args()
    _authenticate_and_print(args.token_file)
