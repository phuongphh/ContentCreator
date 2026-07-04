from __future__ import annotations

"""
Channel registry — nguồn sự thật duy nhất (single source of truth) cho mọi
destination mà pipeline có thể đăng nội dung lên.

Mọi module khác (uploader, scheduler, analytics, ...) nên `import channels`
thay vì hard-code tên kênh / platform. Khi đổi tên kênh hoặc thêm kênh mới,
chỉ cần sửa registry này.

Xem docs/current/phase-1-detailed.md mục 3.1.
"""

from typing import TypedDict


class Channel(TypedDict):
    platform: str               # "youtube" | "tiktok"
    track: str                  # "ai" | "drama" | "mixed"
    name: str                   # Tên hiển thị (placeholder cho tới khi branding chốt)
    format_long: bool           # Có đăng video dài không
    format_shorts: bool         # Có đăng video ngắn/Shorts không
    oauth_token_env: str        # Tên biến môi trường chứa OAuth token cho kênh này
    tts_voice_profile: str      # Voice profile TTS mặc định cho kênh này


# Channel registry - source of truth cho mọi destination
CHANNELS: dict[str, Channel] = {
    "ai_youtube": {
        "platform": "youtube",
        "track": "ai",
        "name": "[2P] AI Hôm Nay",             # Gmail owner: 2p.broadcast@gmail.com
        "format_long": True,
        "format_shorts": True,
        "oauth_token_env": "YOUTUBE_AI_TOKEN",
        "tts_voice_profile": "neutral_female",
    },
    "drama_youtube": {
        "platform": "youtube",
        "track": "drama",
        "name": "[2P] Chuyện Đời",              # Gmail owner: 2p.drama@gmail.com
        "format_long": True,
        "format_shorts": True,
        "oauth_token_env": "YOUTUBE_DRAMA_TOKEN",
        "tts_voice_profile": "storyteller_female",
    },
    "tiktok_main": {
        "platform": "tiktok",
        "track": "mixed",                      # cả 2 track đăng cùng tài khoản
        "name": "@phuong.contentlab",          # TODO: cập nhật đúng handle thật (account 2p.broadcast@gmail.com)
        "format_long": False,
        "format_shorts": True,
        "oauth_token_env": "TIKTOK_TOKEN",
        "tts_voice_profile": "auto",           # chọn theo track của video
    },
}


def get_channel(key: str) -> Channel:
    """Lookup a channel by its registry key.

    Raises:
        ValueError: nếu `key` không tồn tại trong registry.
    """
    if key not in CHANNELS:
        raise ValueError(f"Channel {key} not in registry")
    return CHANNELS[key]


def channels_for_track(track: str) -> dict[str, Channel]:
    """Tất cả channel nhận nội dung của `track` (bao gồm channel 'mixed')."""
    return {
        key: channel
        for key, channel in CHANNELS.items()
        if channel["track"] in (track, "mixed")
    }


def channels_for_platform(platform: str) -> dict[str, Channel]:
    """Tất cả channel thuộc một platform (vd 'youtube', 'tiktok')."""
    return {
        key: channel
        for key, channel in CHANNELS.items()
        if channel["platform"] == platform
    }


if __name__ == "__main__":
    for key, channel in CHANNELS.items():
        print(f"{key}: {channel['name']} ({channel['platform']}, track={channel['track']})")
