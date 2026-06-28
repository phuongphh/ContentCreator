import os
from dotenv import load_dotenv

load_dotenv(override=True)

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")
PRODUCTHUNT_API_TOKEN = os.getenv("PRODUCTHUNT_API_TOKEN", "")

# RSS Feeds
RSS_FEEDS = [
    # AI Newsletters
    "https://tldr.tech/api/rss/ai",                          # TLDR AI daily newsletter
    "https://www.bensbites.com/feed",                         # Ben's Bites (moved to Substack)
    # Tech news - AI sections
    "https://techcrunch.com/category/artificial-intelligence/feed",  # TechCrunch AI
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",  # The Verge AI
    "https://arstechnica.com/ai/feed/",                       # Ars Technica AI
    # Vietnamese tech news
    "https://vnexpress.net/rss/khoa-hoc-cong-nghe.rss",      # VnExpress Công nghệ
    # Reddit
    "https://www.reddit.com/r/ChatGPT/.rss",                 # Reddit r/ChatGPT
    "https://www.reddit.com/r/artificial/.rss",               # Reddit r/artificial
]

# Twitter accounts to follow
TWITTER_ACCOUNTS = ["OpenAI", "AnthropicAI", "GoogleDeepMind", "sama", "levelsio"]

# Scoring thresholds
SCORE_THRESHOLD_ANALYSIS = 5.5   # Hạ từ 6.5 → 5.5 để có nhiều bài hơn vào phân tích sâu
SCORE_THRESHOLD_NOTIFY = 5.5     # Hạ từ 7.0 → 5.5 để đảm bảo ít nhất 5 bài vào báo cáo

# Time decay — giảm điểm bài cũ khi chọn cho báo cáo (không thay đổi điểm lưu trong DB)
# final_score = ai_score × exp(-SCORE_DECAY_RATE × days_old), tối thiểu 0.05
# Với rate=0.23: hôm qua ×0.79, 2 ngày ×0.63, 3 ngày ×0.50, 7 ngày ×0.20
SCORE_DECAY_RATE = 0.23

# Limits
MAX_ARTICLES_PER_RUN = 50
MAX_DEEP_ANALYSIS = 10           # Tăng từ 5 → 10 để có đủ bài cho resume top 5
TOP_RESUME_COUNT = 5             # Số bài tối đa trong bản resume gửi Telegram

# Database
DB_PATH = os.path.join(os.path.dirname(__file__), "storage", "content.db")

# --- Video Pipeline ---
TTS_API_URL = os.getenv("TTS_API_URL", "http://tts.nuitruc.ai/api/tts")
TTS_API_KEY = os.getenv("TTS_API_KEY", "")           # Optional
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "voice1")
TTS_VOICE_SPEED = float(os.getenv("TTS_VOICE_SPEED", "1.0"))
# TTS HTTP tuning (issue #58). A black-hole endpoint (TCP connect OK but no
# response) used to stall the whole cron window: 400s timeout × 3 retries
# ≈ 20 min before the fallback provider even ran. Defaults now fail fast and let
# the provider fallback chain (factory) take over. Raise TTS_TIMEOUT only if you
# run a genuinely slow self-hosted backend.
TTS_TIMEOUT = int(os.getenv("TTS_TIMEOUT", "120"))        # per-request socket timeout (s)
TTS_MAX_RETRIES = int(os.getenv("TTS_MAX_RETRIES", "3"))  # retries for fast transient HTTP errors
TTS_RETRY_DELAY = int(os.getenv("TTS_RETRY_DELAY", "5"))  # initial backoff (s), exponential
# Núi Trúc async job API: long scripts no longer fit the old synchronous
# /api/tts (it timed out). The client now submits a job, polls /status, then
# downloads /result. These knobs bound the polling so a job that never finishes
# fails over to the next provider (issue #58) instead of stalling the cron run.
TTS_REQUEST_TIMEOUT = int(os.getenv("TTS_REQUEST_TIMEOUT", "30"))  # submit/status socket timeout (s)
TTS_POLL_INTERVAL = int(os.getenv("TTS_POLL_INTERVAL", "12"))      # seconds between status polls
TTS_POLL_TIMEOUT = int(os.getenv("TTS_POLL_TIMEOUT", "600"))       # max total wait for a job (s)
TTS_POLL_MAX_FAILURES = int(os.getenv("TTS_POLL_MAX_FAILURES", "3"))  # consecutive poll errors before failover
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")     # Free API key from pexels.com/api

# Video output
VIDEO_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
BG_VIDEO_LANDSCAPE = os.path.join(os.path.dirname(__file__), "video", "assets", "backgrounds", "landscape.mp4")
BG_VIDEO_PORTRAIT = os.path.join(os.path.dirname(__file__), "video", "assets", "backgrounds", "portrait.mp4")
SUBTITLE_FONT = os.path.join(os.path.dirname(__file__), "video", "assets", "fonts", "NotoSans-Bold.ttf")
SUBTITLE_FONTSIZE_LONG = 48     # Font size for YouTube landscape
SUBTITLE_FONTSIZE_SHORT = 64    # Font size for Shorts/TikTok

# --- Publisher ---
YOUTUBE_CLIENT_SECRETS = os.getenv("YOUTUBE_CLIENT_SECRETS", "")   # Path to OAuth2 client_secret.json
YOUTUBE_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "publisher", ".youtube_token.json")
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "")

# --- Video engine flags (Video Enhancement roadmap; default = legacy behaviour) ---
# Each flag has a "legacy" default so the pipeline behaves exactly as before
# unless explicitly opted in. See docs/current/video-enhancement/.
#
# SUBTITLE_TIMING_MODE: "wordcount" (legacy, proportional) | "whisper" (P1, audio-aligned)
SUBTITLE_TIMING_MODE = os.getenv("SUBTITLE_TIMING_MODE", "wordcount")
# BACKGROUND_MODE: "single" (legacy, one looped clip) | "multi" (P1, multi-clip)
BACKGROUND_MODE = os.getenv("BACKGROUND_MODE", "single")
# TTS_PROVIDER: "nuitruc" (legacy) | "edge" (P2, Edge TTS vi-VN)
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "nuitruc")
# Edge TTS voice (P2) — used when TTS_PROVIDER=edge or as a fallback provider.
EDGE_VOICE = os.getenv("EDGE_VOICE", "vi-VN-HoaiMyNeural")  # or vi-VN-NamMinhNeural
# COMPOSER_ENGINE: "ffmpeg" (legacy/default) | "moviepy" (P2)
COMPOSER_ENGINE = os.getenv("COMPOSER_ENGINE", "ffmpeg")
# ENABLE_BGM: mix royalty-free background music under narration (P1)
ENABLE_BGM = os.getenv("ENABLE_BGM", "0") == "1"
# TTS_ALLOW_INSECURE_SSL: disable TLS verification for the TTS endpoint.
# SECURITY: only enable for a known self-signed endpoint you trust. Default OFF.
TTS_ALLOW_INSECURE_SSL = os.getenv("TTS_ALLOW_INSECURE_SSL", "0") == "1"
# Whisper model size for subtitle alignment when SUBTITLE_TIMING_MODE=whisper.
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
# Multi-clip background: switch clip roughly every N seconds (BACKGROUND_MODE=multi).
BG_CLIP_SECONDS = int(os.getenv("BG_CLIP_SECONDS", "6"))
BG_CLIP_COUNT = int(os.getenv("BG_CLIP_COUNT", "6"))  # max distinct clips to gather
# Background music (ENABLE_BGM=1): directory + level under the narration.
MUSIC_DIR = os.path.join(os.path.dirname(__file__), "video", "assets", "music")
BGM_VOLUME_DB = float(os.getenv("BGM_VOLUME_DB", "-18"))  # music gain relative to voice

# Allowed values for the string-valued flags above (used by validate_flags()).
_FLAG_CHOICES = {
    "SUBTITLE_TIMING_MODE": {"wordcount", "whisper"},
    "BACKGROUND_MODE": {"single", "multi"},
    "TTS_PROVIDER": {"nuitruc", "edge"},
    "COMPOSER_ENGINE": {"ffmpeg", "moviepy"},
}


def validate_flags(logger=None):
    """Warn about out-of-range video flag values, returning the list of issues.

    Does not raise — an unknown value simply logs a warning and the consuming
    code falls back to its legacy path. Returns a list of human-readable
    warning strings (empty when all flags are valid).
    """
    issues = []
    current = {
        "SUBTITLE_TIMING_MODE": SUBTITLE_TIMING_MODE,
        "BACKGROUND_MODE": BACKGROUND_MODE,
        "TTS_PROVIDER": TTS_PROVIDER,
        "COMPOSER_ENGINE": COMPOSER_ENGINE,
    }
    for name, value in current.items():
        allowed = _FLAG_CHOICES[name]
        if value not in allowed:
            msg = (f"{name}={value!r} is not one of {sorted(allowed)}; "
                   f"falling back to legacy behaviour")
            issues.append(msg)
            if logger is not None:
                logger.warning("Invalid video flag: %s", msg)
    return issues


# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
