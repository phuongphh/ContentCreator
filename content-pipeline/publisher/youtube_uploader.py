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
import time

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from channels import get_channel
from storage.quota import (
    UNITS_VIDEO_INSERT, UNITS_THUMBNAIL_SET, UNITS_CAPTION_INSERT,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # Required for captions().insert (uploading a caption/subtitle track).
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# categoryId theo track (Phase 5 — multi-channel). Khác phase-5-detailed.md
# (đề xuất 22 cho AI): track AI giữ nguyên 28 "Science & Technology" mà kênh
# đang dùng từ trước (đổi category giữa chừng không có lợi ích gì); Drama
# dùng 24 "Entertainment" như doc.
_CATEGORY_BY_TRACK = {"ai": "28", "drama": "24"}

# HTTP status coi là transient khi upload resumable — retry với backoff.
_TRANSIENT_HTTP_STATUS = {429, 500, 502, 503, 504}
_MAX_CHUNK_RETRIES = 4
_RETRY_BASE_DELAY = 2  # 2s, 4s, 8s, 16s


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

        # os.path.dirname("") for a bare filename (e.g. --token-file
        # .youtube_token_drama.json) — makedirs("") raises FileNotFoundError,
        # so fall back to the current directory.
        os.makedirs(os.path.dirname(token_file) or ".", exist_ok=True)
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
        response = _execute_resumable(request)
        _record_quota_safe(UNITS_VIDEO_INSERT, note="videos.insert (legacy upload_video)")

        video_id = response["id"]
        url = f"https://youtu.be/{video_id}"
        logger.info("YouTube upload complete: %s", url)
        return url

    except Exception as e:
        logger.error("YouTube upload failed: %s", e)
        return None


def _is_transient_upload_error(exc: Exception) -> bool:
    """Lỗi tạm thời (đáng retry) khi upload: HTTP 5xx/429 hoặc lỗi mạng."""
    try:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            return getattr(exc.resp, "status", None) in _TRANSIENT_HTTP_STATUS
    except ImportError:
        pass
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _execute_resumable(request):
    """Run a resumable-upload request to completion, retrying transient errors.

    Backoff 2s → 4s → 8s → 16s (per repo git-push convention); resumable
    upload giữ tiến độ giữa các lần next_chunk nên retry không upload lại
    từ đầu. Lỗi không transient (401, 403 quota, 400 metadata) raise ngay.
    """
    response = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload progress: %d%%", int(status.progress() * 100))
            retries = 0  # tiến độ mới = reset đếm retry
        except Exception as e:
            if not _is_transient_upload_error(e) or retries >= _MAX_CHUNK_RETRIES:
                raise
            wait = _RETRY_BASE_DELAY * (2 ** retries)
            retries += 1
            logger.warning("Transient upload error (retry %d/%d in %ds): %s",
                           retries, _MAX_CHUNK_RETRIES, wait, e)
            time.sleep(wait)
    return response


def _record_quota_safe(units: int, note: str | None = None) -> None:
    """Ghi quota usage; lỗi (DB chưa migrate 006, ...) không làm hỏng upload."""
    try:
        from storage.quota import record_youtube_units
        record_youtube_units(units, note=note)
    except Exception as e:
        logger.warning("Quota tracking failed (non-fatal): %s", e)


def resolve_token_file(channel_key: str) -> str:
    """Đường dẫn token OAuth cho 1 kênh trong channels.py.

    Khác phase-5-detailed.md (đề xuất hardcode `tokens/{channel_key}.json`):
    codebase đã có convention từ Phase 1 — channels.py khai báo tên env var
    (`oauth_token_env`), .env trỏ env var đó tới file token (xem
    docs/current/oauth-setup.md §1.4). Dùng đúng convention đó thay vì thêm
    một chỗ hardcode thứ hai. Env var rỗng → fallback token đơn-kênh cũ
    (YOUTUBE_TOKEN_FILE) kèm warning, để setup 1-kênh hiện tại vẫn chạy.
    """
    channel = get_channel(channel_key)
    env_name = channel["oauth_token_env"]
    token_file = getattr(config, env_name, "") or os.getenv(env_name, "")
    if not token_file:
        logger.warning(
            "%s (env %s) chưa cấu hình — dùng token mặc định %s. "
            "Xem docs/current/oauth-setup.md để cấp token riêng cho kênh.",
            channel_key, env_name, config.YOUTUBE_TOKEN_FILE,
        )
        return config.YOUTUBE_TOKEN_FILE
    return token_file


def _split_hashtags(hashtags: str) -> list[str]:
    """'#mẹchồng #drama' → ['mẹchồng', 'drama'] (bỏ dấu #, bỏ rỗng)."""
    return [t.lstrip("#") for t in (hashtags or "").split() if t.lstrip("#")]


def _build_video_body(video: dict, channel_key: str) -> dict:
    """snippet/status body cho videos().insert từ 1 row `videos` + kênh đích."""
    channel = get_channel(channel_key)
    track = video.get("track") or channel["track"]
    is_short = video.get("video_type") == "short"

    title = (video.get("youtube_title")
             or video.get("tiktok_caption")
             or f"Video #{video['id']}")
    if is_short and "#Shorts" not in title:
        title = f"{title} #Shorts"

    if track == "drama":
        tags = ["chuyện đời", "drama", "tâm sự", "kể chuyện"]
    else:
        tags = ["AI", "AI Việt Nam", "công nghệ", "AI 5 phút"]
    tags += _split_hashtags(video.get("tiktok_hashtags", ""))
    if is_short:
        tags.append("Shorts")
    # dedupe, giữ thứ tự
    tags = list(dict.fromkeys(tags))

    return {
        "snippet": {
            "title": title[:100],
            "description": video.get("youtube_description", "") or "",
            "tags": tags,
            "categoryId": _CATEGORY_BY_TRACK.get(track, "28"),
            "defaultLanguage": "vi",
        },
        "status": {
            "privacyStatus": config.YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }


def upload_to_youtube(video_id: int, channel_key: str) -> dict | None:
    """Upload 1 video trong DB lên đúng kênh YouTube theo channel registry.

    Phase 5 EPIC #5.2. Chọn OAuth token theo `channels.py[channel_key]`,
    upload resumable có retry, rồi (best-effort) set thumbnail riêng nếu
    video có `thumbnail_path`. Caption track được upload khi video KHÔNG
    burn phụ đề (cùng chính sách với main._publish_to_platform).

    Returns:
        {"youtube_video_id": ..., "url": ...} khi thành công — caller
        (scheduler) phải lưu id này NGAY để chống upload trùng; None on failure.
    """
    from storage.database import get_video

    video = get_video(video_id)
    if not video:
        logger.error("upload_to_youtube: video %d not found", video_id)
        return None
    video_path = video.get("video_path")
    if not video_path or not os.path.exists(video_path):
        logger.error("upload_to_youtube: file missing for video %d: %s",
                     video_id, video_path)
        return None

    channel = get_channel(channel_key)
    if channel["platform"] != "youtube":
        logger.error("upload_to_youtube: %s is not a YouTube channel", channel_key)
        return None

    token_file = resolve_token_file(channel_key)
    service = _get_authenticated_service(token_file)
    if not service:
        logger.error("upload_to_youtube: authentication failed for %s (token %s)",
                     channel_key, token_file)
        return None

    from googleapiclient.http import MediaFileUpload
    body = _build_video_body(video, channel_key)
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)

    try:
        request = service.videos().insert(
            part="snippet,status", body=body, media_body=media,
        )
        response = _execute_resumable(request)
    except Exception as e:
        logger.error("upload_to_youtube failed for video %d → %s: %s",
                     video_id, channel_key, e)
        return None
    finally:
        _record_quota_safe(UNITS_VIDEO_INSERT,
                           note=f"videos.insert video={video_id} channel={channel_key}")

    youtube_video_id = response["id"]
    url = f"https://youtu.be/{youtube_video_id}"
    logger.info("Uploaded video %d to %s (%s): %s",
                video_id, channel_key, channel["name"], url)

    # Thumbnail riêng (EPIC #5.2): best-effort — video đã lên sóng, thumbnail
    # hỏng chỉ log warning chứ không coi là upload thất bại.
    thumbnail_path = video.get("thumbnail_path")
    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            service.thumbnails().set(
                videoId=youtube_video_id,
                media_body=MediaFileUpload(thumbnail_path),
            ).execute()
            _record_quota_safe(UNITS_THUMBNAIL_SET,
                               note=f"thumbnails.set video={video_id}")
            logger.info("Thumbnail set for %s", youtube_video_id)
        except Exception as e:
            logger.warning("Thumbnail upload failed for %s (non-fatal): %s",
                           youtube_video_id, e)

    # Caption track khi phụ đề không burn (cùng chính sách với track AI cũ) —
    # nhưng KHÔNG fail cả publish ở đây: scheduler đã lưu youtube_video_id,
    # fail sau điểm này chỉ nên alert chứ không kích hoạt re-upload.
    burned = video.get("subtitles_burned")
    if burned is None:
        burned = config.should_burn_subtitles(video.get("video_type", "short"))
    srt = video.get("subtitle_path")
    if not burned and srt and os.path.exists(srt):
        if upload_caption(url, srt, token_file=token_file):
            _record_quota_safe(UNITS_CAPTION_INSERT,
                               note=f"captions.insert video={video_id}")
        else:
            logger.warning("Caption upload failed for video %d (%s)", video_id, url)

    return {"youtube_video_id": youtube_video_id, "url": url}


def upload_caption(video_url_or_id: str, srt_path: str,
                   language: str = "vi", name: str = "Tiếng Việt",
                   token_file: str | None = None) -> bool:
    """Upload an SRT file as a caption track for an existing YouTube video.

    Lets long videos ship accurate, viewer-toggleable captions (the script text
    we control) instead of burning subtitles into the frame. Returns True on
    success.

    `token_file`: token OAuth của kênh sở hữu video (multi-channel, Phase 5);
    mặc định dùng token đơn-kênh cũ như trước.
    """
    video_id = _video_id_from_url(video_url_or_id)
    if not video_id:
        logger.error("upload_caption: could not resolve video id from %r", video_url_or_id)
        return False
    if not srt_path or not os.path.exists(srt_path):
        logger.error("upload_caption: SRT not found: %s", srt_path)
        return False

    from googleapiclient.http import MediaFileUpload

    service = _get_authenticated_service(token_file)
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
