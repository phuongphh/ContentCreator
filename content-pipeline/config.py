import os
from dotenv import load_dotenv

load_dotenv(override=True)

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Kênh Telegram "Bé MC" nhận video đã render để upload TikTok THỦ CÔNG. TikTok
# không auto-upload nữa: pipeline gửi video vào đây, user tự tải lên TikTok.
# Rỗng → dùng chung TELEGRAM_CHAT_ID (không để video rơi vào hư không).
TELEGRAM_TIKTOK_CHAT_ID = os.getenv("TELEGRAM_TIKTOK_CHAT_ID", "")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")
PRODUCTHUNT_API_TOKEN = os.getenv("PRODUCTHUNT_API_TOKEN", "")

# --- HTTP User-Agent dùng chung (issue #97) ---
# Cloudflare chặn User-Agent mặc định "Python-urllib/3.x" của urllib (error
# code 1010) → Pexels/Replicate trả 403 dù key hoàn toàn đúng. MỌI request
# urllib tới các API sau CDN/WAF phải gửi UA này thay vì để urllib tự điền.
# (Reddit/Lemmy có UA riêng theo quy định API của họ — REDDIT_USER_AGENT/
# LEMMY_USER_AGENT bên dưới, không dùng chung cái này.)
HTTP_USER_AGENT = os.getenv(
    "HTTP_USER_AGENT", "Mozilla/5.0 (compatible; ContentPipelineBot/1.0)"
)

# --- Reddit API (issue #78) ---
# Root cause of #78: unauthenticated requests to www.reddit.com/*.json and
# .rss are aggressively rate-limited (429) and blocked (403), especially from
# datacenter IPs. Reddit's supported path is OAuth2. When a client id/secret is
# configured, collectors/reddit_client.py uses the app-only (client_credentials)
# grant against oauth.reddit.com — a documented ~100 req/min budget that bypasses
# the block.
#
# HOWEVER (issue #78 follow-up): Reddit killed *self-service* API app creation in
# Nov 2025 (Responsible Builder Policy) — a fresh install can no longer create an
# app to get these credentials without a manual, multi-week approval that
# routinely rejects small personal projects. So Reddit collection is now OFF by
# default (REDDIT_ENABLED below): collectors skip Reddit entirely rather than hit
# the unauthenticated endpoints, which both fail AND re-flag the source IP. The
# Drama track instead runs on manual seeds (notifier/seed_bot.py) + other sources.
#
# If you DO obtain approved OAuth credentials, set REDDIT_ENABLED=1 and fill
# REDDIT_CLIENT_ID/SECRET — collection resumes via oauth.reddit.com with no other
# code change. The User-Agent MUST be unique and descriptive per Reddit's API
# rules. Format: <platform>:<app id>:<version> (by /u/<your-username>).
REDDIT_ENABLED = os.getenv("REDDIT_ENABLED", "0") == "1"
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT",
    "python:ai5phut-content-pipeline:1.1 (by /u/ai5phut_bot)",
)
REDDIT_TIMEOUT = int(os.getenv("REDDIT_TIMEOUT", "15"))          # per-request socket timeout (s)
REDDIT_MAX_RETRIES = int(os.getenv("REDDIT_MAX_RETRIES", "3"))   # retries for 429/5xx/network
REDDIT_RETRY_BACKOFF = int(os.getenv("REDDIT_RETRY_BACKOFF", "2"))  # backoff base (s): 2, 4, 8...
# Minimum spacing between Reddit calls. With OAuth the budget is ~100 req/min
# so 1s is plenty; without OAuth we go slower to avoid tripping the throttle.
REDDIT_MIN_INTERVAL = float(os.getenv("REDDIT_MIN_INTERVAL", "1.0"))
# Cap how long a 429 Retry-After may park the run — Reddit usually returns a few
# seconds, but we never want a pathological value to stall the whole cron window.
REDDIT_RETRY_AFTER_CAP = float(os.getenv("REDDIT_RETRY_AFTER_CAP", "60"))

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
    # NOTE: Reddit r/ChatGPT + r/artificial are collected by
    # collectors/reddit_collector.py via the authenticated JSON API (issue #78),
    # NOT here — the www.reddit.com/*.rss endpoints are blocked (403/429) for
    # unauthenticated clients and duplicated those two subreddits anyway.
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
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "preset_my_duyen")
TTS_VOICE_SPEED = float(os.getenv("TTS_VOICE_SPEED", "1.0"))
# Per-track voice + speed. Each channel reads its own pair so the two channels
# can sound distinct without a global toggle:
#   ai_youtube ([2P] AI Hôm Nay)  → voice "voice1",          speed 1.5
#   drama_youtube ([2P] Chuyện Đời)→ voice "preset_my_duyen", speed 1.0
# All four are env-overridable (.env wins). An EMPTY voice id means "no
# override — fall back to the provider's own default voice"; speed always has a
# numeric default so a blank env var can't produce a crash-y float("").
TTS_VOICE_ID_AI = os.getenv("TTS_VOICE_ID_AI", "voice1")
TTS_VOICE_SPEED_AI = float(os.getenv("TTS_VOICE_SPEED_AI") or "1.5")
TTS_VOICE_ID_DRAMA = os.getenv("TTS_VOICE_ID_DRAMA", "preset_my_duyen")
TTS_VOICE_SPEED_DRAMA = float(os.getenv("TTS_VOICE_SPEED_DRAMA") or "1.0")
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

# --- AI illustration generation (Phase 4 — Drama Visual Assets) ---
# Replaces Pexels stock footage for Drama scenes (stock clips don't fit drama
# story illustration well) via Replicate's prediction API. Optional: with no
# token configured, video/image_generator.py returns None and the composer
# falls back to a solid/gradient background — never a hard failure.
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
# Model version hash to run (see replicate.com/<model>/versions). Left empty
# by default — pick and pin a specific image model/version before enabling.
REPLICATE_MODEL_VERSION = os.getenv("REPLICATE_MODEL_VERSION", "")

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

# --- Multi-channel credentials (Phase 1 — Multi-channel Foundation) ---
# Referenced by channels.py CHANNELS[key]["oauth_token_env"]; multi-channel
# upload routing that reads these lands in Phase 5. Kept here (not hard-coded
# per-destination) so channels.py remains the single source of truth.
YOUTUBE_AI_TOKEN = os.getenv("YOUTUBE_AI_TOKEN", "")
YOUTUBE_AI_CHANNEL_ID = os.getenv("YOUTUBE_AI_CHANNEL_ID", "")
YOUTUBE_DRAMA_TOKEN = os.getenv("YOUTUBE_DRAMA_TOKEN", "")
YOUTUBE_DRAMA_CHANNEL_ID = os.getenv("YOUTUBE_DRAMA_CHANNEL_ID", "")
TIKTOK_TOKEN = os.getenv("TIKTOK_TOKEN", "")
TIKTOK_OPEN_ID = os.getenv("TIKTOK_OPEN_ID", "")

# --- Distribution (Phase 5) ---
# privacyStatus khi upload YouTube. Đặt "unlisted" khi chạy E2E test để video
# test không public (phase-5-issues.md EPIC #5.2).
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "public")
# Quota YouTube Data API v3 mỗi ngày/project + ngưỡng alert (storage/quota.py).
YOUTUBE_DAILY_QUOTA = int(os.getenv("YOUTUBE_DAILY_QUOTA", "10000"))
QUOTA_ALERT_RATIO = float(os.getenv("QUOTA_ALERT_RATIO", "0.8"))
# Số story Drama tối đa render thành video mỗi lần chạy main_drama.py
# (kiểm soát chi phí TTS/render, giống MAX_DEEP_ANALYSIS cho track AI).
DRAMA_VIDEOS_PER_RUN = int(os.getenv("DRAMA_VIDEOS_PER_RUN", "2"))
# Thư mục queue cho TikTok upload tay (publisher/tiktok_manual.py).
TIKTOK_QUEUE_DIR = os.path.join(os.path.dirname(__file__), "queue_tiktok")
# Health endpoint (webui/health.py) — chỉ bind 127.0.0.1.
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8686"))

# --- OAuth token health check (issue #94) ---
# Giám sát OAuth token của MỌI kênh YouTube trong channels.py (không chỉ 1 file
# cứng như cron cũ). publisher/token_health.py probe refresh_token qua stdlib
# urllib với socket timeout có giới hạn — KHÔNG dùng creds.refresh() của
# google-auth (không đặt timeout → treo vô hạn, chính là nguyên nhân cron cũ
# timeout 30s rồi bị kill 13 lần liên tiếp, issue #94 mục 3).
# Socket timeout (giây) cho mỗi lần probe refresh — fail-fast thay vì treo.
TOKEN_HEALTH_TIMEOUT = int(os.getenv("TOKEN_HEALTH_TIMEOUT", "15"))
# Số lần probe TRANSIENT (mạng/timeout/5xx) liên tiếp trước khi alert rằng
# CHÍNH bộ giám sát không tới được Google (để "monitor chết" không im lặng như
# 13 lần fail của cron cũ). 0 = không đếm/không alert transient.
TOKEN_HEALTH_TRANSIENT_ALERT_AFTER = int(
    os.getenv("TOKEN_HEALTH_TRANSIENT_ALERT_AFTER", "3")
)

# --- Media asset API key health check (follow-up #94) ---
# Giám sát API key TĨNH của nhà cung cấp asset video: Pexels (nền b-roll, dùng
# cả 2 track) và Replicate (minh hoạ AI, chỉ track Drama). Khác token YouTube:
# đây là key bearer tĩnh (không refresh), nên video/asset_key_health.py chỉ gọi
# 1 request xác thực nhẹ (200 = ok, 401/403 = key hỏng) — cùng tinh thần
# fail-fast + phân biệt lỗi cứng/tạm thời của token_health.
ASSET_KEY_HEALTH_TIMEOUT = int(os.getenv("ASSET_KEY_HEALTH_TIMEOUT", "15"))
ASSET_KEY_HEALTH_TRANSIENT_ALERT_AFTER = int(
    os.getenv("ASSET_KEY_HEALTH_TRANSIENT_ALERT_AFTER", "3")
)

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
# BURN_SUBTITLES: which video types get hard-burned subtitles.
#   all (legacy)  — burn into every video
#   short_only    — burn only short (TikTok/Shorts, watched sound-off); long
#                   videos skip burn-in and upload an SRT caption track to YouTube
#   none          — never burn (rely on caption tracks / platform CC)
BURN_SUBTITLES = os.getenv("BURN_SUBTITLES", "all")
# TTS_ALLOW_INSECURE_SSL: disable TLS verification for the TTS endpoint.
# SECURITY: only enable for a known self-signed endpoint you trust. Default OFF.
TTS_ALLOW_INSECURE_SSL = os.getenv("TTS_ALLOW_INSECURE_SSL", "0") == "1"
# Whisper model size for subtitle alignment when SUBTITLE_TIMING_MODE=whisper.
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
# Multi-clip background: switch clip roughly every N seconds (BACKGROUND_MODE=multi).
BG_CLIP_SECONDS = int(os.getenv("BG_CLIP_SECONDS", "6"))
BG_CLIP_COUNT = int(os.getenv("BG_CLIP_COUNT", "6"))  # max distinct clips to gather
# Background variety (anti-repeat): when a duration-matched clip is picked, choose
# randomly among the BG_VARIETY_TOPK closest-fit clips and avoid the last
# BG_RECENT_WINDOW clips already used. This stops same-length videos (e.g. the
# ~60s shorts that all match the same closest clip) from reusing one background.
# Set BG_VARIETY_TOPK=1 to restore the old deterministic closest-fit pick.
BG_VARIETY_TOPK = int(os.getenv("BG_VARIETY_TOPK", "3"))
BG_RECENT_WINDOW = int(os.getenv("BG_RECENT_WINDOW", "8"))
# Background music (ENABLE_BGM=1): directory + level under the narration.
MUSIC_DIR = os.path.join(os.path.dirname(__file__), "video", "assets", "music")
# Drama track uses its own pool (tense/dramatic loops) instead of the AI
# track's — templates (video/templates/drama.py) name a preferred track by
# filename, with a random pick from this dir as fallback.
DRAMA_MUSIC_DIR = os.path.join(os.path.dirname(__file__), "video", "assets", "music_drama")
BGM_VOLUME_DB = float(os.getenv("BGM_VOLUME_DB", "-18"))  # music gain relative to voice

# Allowed values for the string-valued flags above (used by validate_flags()).
_FLAG_CHOICES = {
    "SUBTITLE_TIMING_MODE": {"wordcount", "whisper"},
    "BACKGROUND_MODE": {"single", "multi"},
    "TTS_PROVIDER": {"nuitruc", "edge"},
    "COMPOSER_ENGINE": {"ffmpeg", "moviepy"},
    "BURN_SUBTITLES": {"all", "short_only", "none"},
}


def tts_profile_for_track(track: str) -> tuple[str | None, float]:
    """(voice_id, speed) TTS cho một track ('ai' | 'drama').

    Single source of truth để tts_client không phải rải các nhánh if/else về
    voice/speed khắp nơi. voice_id rỗng → None (dùng voice mặc định của
    provider); track lạ → dùng voice/speed global (TTS_VOICE_ID/TTS_VOICE_SPEED)
    thay vì đoán, giữ hành vi an toàn cho code cũ.
    """
    table = {
        "ai": (TTS_VOICE_ID_AI, TTS_VOICE_SPEED_AI),
        "drama": (TTS_VOICE_ID_DRAMA, TTS_VOICE_SPEED_DRAMA),
    }
    voice_id, speed = table.get(track, (TTS_VOICE_ID, TTS_VOICE_SPEED))
    return (voice_id or None), speed


def should_burn_subtitles(video_type: str) -> bool:
    """Whether to hard-burn subtitles for this video type (per BURN_SUBTITLES).

    Unknown values fall back to legacy "all" (burn everything).
    """
    mode = BURN_SUBTITLES
    if mode == "none":
        return False
    if mode == "short_only":
        return video_type == "short"
    return True


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
        "BURN_SUBTITLES": BURN_SUBTITLES,
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


# --- Drama prompt versioning (Phase 3) ---
# Selects prompts/{module}/{name}.{version}.txt via processors/prompt_loader.py.
# Rollback = change this env var, no code change needed.
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")

# Per-prompt override for the rewriter only. PROMPT_VERSION is global (scorer/
# theme_detect/longform all resolve through it), so bumping it to v2 would
# demand a v2 file for every prompt. The rewriter moved to v2 alone (2-3 minute
# short target — see the word-count bands below); rollback to the old 6-minute
# format = DRAMA_REWRITER_PROMPT_VERSION=v1.
DRAMA_REWRITER_PROMPT_VERSION = os.getenv("DRAMA_REWRITER_PROMPT_VERSION", "v2")

# Drama rubric scoring threshold (out of 6 criteria) — story must score
# >= this AND safe=1 to proceed to the rewriter.
#
# Lowered 5 -> 4 (issue #86 follow-up): at 5/6, since safe=1 is mandatory, a
# story had to hit 4 of the 5 *content* criteria (hook/stakes/twist/localizable/
# comment_bait). Real Lemmy drama (relationship_advice/aita/asklemmy) rarely has
# a cinematic TWIST, so most stories capped at 4/6 and the whole day's ~4-story
# batch scored 0 passes -> 0 videos. 4/6 = safe + any 3 content signals, a
# realistic bar for genuine drama. The rubric prompt was also re-tuned so TWIST
# accepts an escalation/reveal (not only a plot reversal) and SAFE gates graphic
# *depiction*, not conflict *themes* (see prompts/drama/scorer.v1.txt). Drop to 3
# via env on a very thin source day; raise back to 5 if quality dips. A human
# review gate (review_bot) still stands between here and publish.
DRAMA_SCORE_THRESHOLD = int(os.getenv("DRAMA_SCORE_THRESHOLD", "4"))

# Output token ceiling for drama_rewriter's Sonnet call (issue #82). The
# rewriter emits the Vietnamese script + vn_commentary + title/hook/
# thumbnail_prompt/tags, all inside a JSON wrapper. Vietnamese tokenizes at
# roughly ~2 tokens/word (diacritics); the old 2000 ceiling truncated the
# 800-1200-word v1 format mid-JSON (stop_reason='max_tokens'), yielding the
# misleading "No JSON object found". The v2 short target (~400-550 words total)
# fits comfortably under 4096; keeping the ceiling unchanged costs nothing
# (only actual output tokens bill) and still covers a v1 rollback. The rewriter
# escalates from here (x1.5, x2) if a run still truncates.
DRAMA_REWRITER_MAX_TOKENS = int(os.getenv("DRAMA_REWRITER_MAX_TOKENS", "4096"))

# Drama rewriter script word-count validation (issue #86) — two bands:
#   - [SOFT_MIN, SOFT_MAX]                    ideal → approve cleanly
#   - [HARD_MIN, SOFT_MIN) or (SOFT_MAX, HARD_MAX]
#                                             short/long of ideal but still a
#                                             real, complete script → approve
#                                             with a logged note (observability)
#   - < HARD_MIN or > HARD_MAX                genuinely broken (truncated stub /
#                                             runaway or looping output) → reject
# Recalibrated for the 2-3 MINUTE short target (channel-owner request): the old
# 800-1200-word v1 prompt produced ~6-minute videos — measured on real output,
# the drama TTS voice speaks ~210-230 words/minute, NOT the "60-90s" the v1
# prompt claimed (same class of word-count/duration doc error as Phase 3/4).
# Total narration for 2-3 min = ~420-650 words; the script's share (after hook
# ~20 + reactions ~50 + commentary ~100) is ideally 250-400. Floor 150 still
# rejects truncated stubs; ceiling 600 rejects runaway output that would push
# the video back past ~3.5 minutes. The prompt (rewriter.v2.txt) aims for
# 250-400 (aspirational) — the bands are what we *accept*. All env-overridable.
DRAMA_SCRIPT_SOFT_MIN_WORDS = int(os.getenv("DRAMA_SCRIPT_SOFT_MIN_WORDS", "250"))
DRAMA_SCRIPT_SOFT_MAX_WORDS = int(os.getenv("DRAMA_SCRIPT_SOFT_MAX_WORDS", "400"))
DRAMA_SCRIPT_HARD_MIN_WORDS = int(os.getenv("DRAMA_SCRIPT_HARD_MIN_WORDS", "150"))
DRAMA_SCRIPT_HARD_MAX_WORDS = int(os.getenv("DRAMA_SCRIPT_HARD_MAX_WORDS", "600"))

# Minimum vn_commentary length. Was a hardcoded 200-word module constant sized
# for the ~6-minute v1 format; at 2-3 minutes total, 200 words of commentary
# alone would eat half the video. 60 words keeps the "unique Vietnamese
# perspective" transformative beat (still ~20% of a ~2.5-minute narration)
# without crowding out the story. Env-overridable like the script bands.
DRAMA_COMMENTARY_MIN_WORDS = int(os.getenv("DRAMA_COMMENTARY_MIN_WORDS", "60"))

# Drama rewriter hook length validation (issue #99) — same two-band design as
# the script word count above (issue #86); the hook was the one length check
# left as a single hard threshold. Story 574's hook came back at 26 words —
# ONE word over the old limit of 25, on an otherwise complete script — and was
# hard-blocked to 'needs_review', which for *stories* is a dead end (review_bot
# reviews videos; rewrite_all_scored deliberately skips needs_review stories).
#   - <= SOFT_MAX          ideal ~3s hook → approve cleanly
#   - (SOFT_MAX, HARD_MAX] slightly long but still a hook → approve + logged note
#   - > HARD_MAX           a paragraph, not a hook → needs_review
# 35 words ≈ 10-12s spoken Vietnamese — past that it stops working as a 3s hook.
DRAMA_HOOK_SOFT_MAX_WORDS = int(os.getenv("DRAMA_HOOK_SOFT_MAX_WORDS", "25"))
DRAMA_HOOK_HARD_MAX_WORDS = int(os.getenv("DRAMA_HOOK_HARD_MAX_WORDS", "35"))

# --- Lemmy (issue #78 follow-up: Reddit-alternative source for Drama) ---
# Lemmy is a federated, open Reddit alternative with a public read API (no
# OAuth, no approval — unlike Reddit post-Nov-2025). Drama stories come out in
# English and get localized by the existing drama_rewriter. Communities are
# "name@instance"; add more via LEMMY_COMMUNITIES (comma-separated). Volume is
# lower than Reddit, so the score bar is modest.
LEMMY_ENABLED = os.getenv("LEMMY_ENABLED", "1") == "1"
LEMMY_INSTANCE = os.getenv("LEMMY_INSTANCE", "https://lemmy.world").rstrip("/")
# Curated set of drama/story communities on lemmy.world. Only *body-story*
# communities (the post body IS the conflict) belong here: relationship_advice +
# aita. asklemmy was REMOVED from the default (issue #90): it is a general Q&A
# board — its top-of-day is everyday questions (music, pets, PCs), zero
# interpersonal drama, so every asklemmy post the collector scored got rejected
# 1-3/6, burning Haiku calls on content that can never pass. Add it back via
# LEMMY_COMMUNITIES only if you want the variety and accept the noise. Lemmy
# drama volume is thin regardless (far below Reddit), so the reliable daily
# drama source is the HuggingFace AITA dump (see HF_DRAMA_* below); Lemmy is the
# best-effort *live* topping. A community that 404s is logged and skipped, not
# fatal — trim any that stay noisy in your logs.
LEMMY_COMMUNITIES = [
    c.strip() for c in os.getenv(
        "LEMMY_COMMUNITIES",
        "relationship_advice@lemmy.world,aita@lemmy.world",
    ).split(",") if c.strip()
]
LEMMY_MIN_SCORE = int(os.getenv("LEMMY_MIN_SCORE", "10"))
LEMMY_USER_AGENT = os.getenv(
    "LEMMY_USER_AGENT", "ai5phut-content-pipeline/1.0 (drama story collector)"
)
LEMMY_TIMEOUT = int(os.getenv("LEMMY_TIMEOUT", "15"))
LEMMY_MAX_RETRIES = int(os.getenv("LEMMY_MAX_RETRIES", "3"))
# AskReddit-style (Q&A) communities: here the value is in the TOP COMMENTS
# (answers), not the post body — like the "câu hỏi + tuyển câu trả lời" threads
# that VN pages repost. For a community in this set, the collector fetches a
# post's top comments and assembles "question + selected answers" into a story;
# body-story communities (relationship_advice/aita) stay body-story mode.
# Default is EMPTY (issue #90): asklemmy — the only Q&A board we had — proved to
# be non-drama noise (see LEMMY_COMMUNITIES). The Q&A machinery stays in place;
# to use it, add a genuinely dramatic Q&A community to LEMMY_COMMUNITIES (so it
# gets fetched) AND list it here (so it's parsed in Q&A mode).
LEMMY_QA_COMMUNITIES = [
    c.strip() for c in os.getenv(
        "LEMMY_QA_COMMUNITIES", ""
    ).split(",") if c.strip()
]
LEMMY_QA_TOP_COMMENTS = int(os.getenv("LEMMY_QA_TOP_COMMENTS", "6"))       # answers per story
LEMMY_QA_MIN_COMMENTS = int(os.getenv("LEMMY_QA_MIN_COMMENTS", "2"))       # min answers to keep
LEMMY_QA_MIN_COMMENT_SCORE = int(os.getenv("LEMMY_QA_MIN_COMMENT_SCORE", "3"))
LEMMY_QA_MIN_COMMENT_CHARS = int(os.getenv("LEMMY_QA_MIN_COMMENT_CHARS", "30"))

# --- HuggingFace drama dataset import (issue #78 follow-up) ---
# Seeds the stories table from a public AITA/relationship dataset via the HF
# datasets-server REST API (stdlib only, no `datasets` dependency). Title/body
# columns are auto-detected; override if a dataset uses odd names.
#
# HF's reliable role is VOLUME/backfill, NOT timeliness. Most "auto-updating"
# AITA datasets have gone stale (e.g. derek-thomas/...-amitheasshole stopped at
# 2023-12-04, ~2.5k rows — Codex review on PR #81), so a daily `--newest` poll of
# them just re-imports the same old tail. Timeliness ("thời sự") comes from
# Lemmy (a live API), not HF. Default here is therefore the big STATIC dump, used
# for a one-off deep backfill cushion:
#   python -m collectors.hf_drama_importer --dataset OsamaBsher/AITA-Reddit-Dataset --limit 500
HF_DRAMA_DATASET = os.getenv("HF_DRAMA_DATASET", "OsamaBsher/AITA-Reddit-Dataset")
HF_DRAMA_CONFIG = os.getenv("HF_DRAMA_CONFIG", "default")
HF_DRAMA_SPLIT = os.getenv("HF_DRAMA_SPLIT", "train")
HF_IMPORT_LIMIT = int(os.getenv("HF_IMPORT_LIMIT", "200"))  # default for the manual tool
HF_TITLE_FIELD = os.getenv("HF_TITLE_FIELD", "")   # "" = auto-detect
HF_BODY_FIELD = os.getenv("HF_BODY_FIELD", "")     # "" = auto-detect
HF_TIMEOUT = int(os.getenv("HF_TIMEOUT", "30"))
# Quality comments. For AITA/drama, the community reaction (the YTA/NTA verdicts +
# the savage top replies) is often the strongest "comment bait" — so when a
# dataset row carries comments, append the best few to the story so the scorer and
# rewriter see them (they read raw_content). Auto-detected like the body column;
# a no-op when the dataset has no comment column. Mirrors the Lemmy Q&A filter.
HF_IMPORT_COMMENTS = os.getenv("HF_IMPORT_COMMENTS", "1") == "1"
HF_COMMENTS_FIELD = os.getenv("HF_COMMENTS_FIELD", "")   # "" = auto-detect
HF_COMMENT_TOP_N = int(os.getenv("HF_COMMENT_TOP_N", "3"))       # keep best N
HF_COMMENT_MIN_SCORE = int(os.getenv("HF_COMMENT_MIN_SCORE", "10"))  # only if scored
HF_COMMENT_MIN_CHARS = int(os.getenv("HF_COMMENT_MIN_CHARS", "40"))  # drop one-liners
# Daily HuggingFace auto-import inside main_drama's collect step. ON by default
# (issue #90): with Reddit off (#78) and Lemmy drama communities near-empty, the
# 270K-row AITA dump is the only reliable well of *genuine* drama — and for a
# drama channel it doesn't matter that these posts aren't "thời sự" (an AITA
# conflict is as engaging in 2026 as in 2019; timeliness is the AI-news track's
# concern, not this one). Set HF_DRAMA_DAILY_ENABLED=0 to turn it off (e.g. once
# Reddit is re-enabled and carrying quality).
HF_DRAMA_DAILY_ENABLED = os.getenv("HF_DRAMA_DAILY_ENABLED", "1") == "1"
# How the daily import walks the dataset:
#   "cursor" (default) — walk FORWARD through the big STATIC dump, a fresh window
#       of unseen rows each day, tracked by a persisted per-dataset offset
#       (storage/pipeline_state.py, migration 008). This is what makes daily HF
#       reliable for a static dump: unlike "newest", it never re-imports the same
#       rows. 270K rows / HF_DAILY_LIMIT per day = years of runway; it wraps back
#       to the start when it reaches the end.
#   "newest" — pull from the TAIL (offset = size - limit). ONLY useful for a
#       dataset you've CONFIRMED is still appended to; against a static dump it
#       re-polls the same stale tail and imports nothing (the old default's flaw).
HF_DRAMA_DAILY_MODE = os.getenv("HF_DRAMA_DAILY_MODE", "cursor").strip().lower()
# Rows to import per daily run. Kept modest to balance the funnel end-to-end:
# import ~N -> scorer keeps the drama ones -> rewriter (Sonnet, the cost driver,
# capped at ~10/day) -> render (~2 videos/day) + a slowly-growing approved
# cushion. Raising this mostly grows the approved backlog and Sonnet spend, not
# the videos/day, so bump it only when you want a deeper cushion.
HF_DAILY_LIMIT = int(os.getenv("HF_DAILY_LIMIT", "10"))

# --- HuggingFace raw-CSV fallback (issue #92) ---
# The datasets-server /rows API is a separate, flaky service that periodically
# returns 503 / a "viewer building" HTML page for large datasets — when it does,
# the daily drama source dries up even though the dataset itself is fine. The Hub
# (huggingface.co) stays up in those outages, and the raw CSV is downloadable via
# Git LFS at /datasets/<ds>/resolve/main/<file>.csv. So when the API is
# unavailable we fall back to reading rows straight from that CSV (cached once on
# disk — the dump is static, per HF_DRAMA_* above, so it never needs refetching).
# Row ORDER and column detection match the API path, so the daily cursor and
# source_id dedupe stay consistent across a mid-run API↔CSV switch.
HF_CSV_FALLBACK_ENABLED = os.getenv("HF_CSV_FALLBACK_ENABLED", "1") == "1"
# "" = auto-discover the CSV filename in the repo via the Hub API (which stays up
# when datasets-server is down). Set to a specific repo path (e.g.
# "cleaned_dataset.csv") to skip discovery or pick among multiple CSVs.
HF_DRAMA_CSV_FILE = os.getenv("HF_DRAMA_CSV_FILE", "")
HF_CSV_CACHE_DIR = os.getenv(
    "HF_CSV_CACHE_DIR", os.path.join(os.path.dirname(__file__), "data", "hf_csv_cache")
)
# Guard against filling the disk with a runaway/hostile download (the AITA dump is
# ~680MB; 2GB leaves headroom). Streaming download aborts past this.
HF_CSV_MAX_BYTES = int(os.getenv("HF_CSV_MAX_BYTES", str(2 * 1024 ** 3)))
# Cached CSV freshness in days; 0 = never expire (the default dump is STATIC, so a
# stale-forever cache is correct). Set >0 only for a dataset you expect to change.
HF_CSV_CACHE_TTL_DAYS = int(os.getenv("HF_CSV_CACHE_TTL_DAYS", "0"))

# --- Google Sheets drama bridge (external-source funnel) ---
# One stable, free ingestion point for every source the pipeline can't (or
# shouldn't) scrape directly: a Google Sheet that external automations write
# rows into, which collect_all_gsheet() reads back as stories.
#   * Make.com Free (1,000 ops/tháng): RSS (Reddit hot/.rss, hoặc bất kỳ feed
#     nào) → Google Sheets "Add a Row". Reddit chặn fetcher datacenter từng đợt
#     (403) — khi Make bị chặn thì scenario đó nghỉ, sheet vẫn còn, pipeline
#     không hỏng: cầu nối này cố ý tách "nguồn cào" khỏi "nguồn nạp".
#   * Dán tay: drama Việt (confession FB, group "hóng biến", truyện dịch) —
#     paste thẳng title + content vào sheet, không cần Make.
# Sheet cần share "Anyone with the link – Viewer" (hoặc File → Share → Publish
# to web → CSV). GSHEET_DRAMA_URL nhận cả link /edit thường lẫn link CSV đã
# publish — collector tự đổi sang dạng export CSV. Rỗng = tắt (mặc định).
GSHEET_DRAMA_URL = os.getenv("GSHEET_DRAMA_URL", "")
# Max NEW stories imported per run (the whole sheet is still scanned for
# dedupe). Keeps one giant paste-dump from flooding a day's Haiku scoring.
GSHEET_IMPORT_LIMIT = int(os.getenv("GSHEET_IMPORT_LIMIT", "30"))
# Rows whose content (after HTML stripping) is shorter than this are skipped —
# link-only RSS items and junk rows carry no story to rewrite.
GSHEET_MIN_BODY_CHARS = int(os.getenv("GSHEET_MIN_BODY_CHARS", "200"))
GSHEET_TIMEOUT = int(os.getenv("GSHEET_TIMEOUT", "30"))
# Download cap — a sheet is small; 20MB means something is wrong (or hostile).
GSHEET_MAX_BYTES = int(os.getenv("GSHEET_MAX_BYTES", str(20 * 1024 * 1024)))

# Drama backlog alert (issue #78 follow-up). With Reddit off by default, the
# Drama channel is fed by manual seeds — so the meaningful health signal is "not
# enough stories queued to keep producing", not "a collector went silent". When
# the producible backlog (pending + approved) drops below this, collector_health
# alerts on Telegram to prompt a /seed_vn. Replaces the old reddit_drama
# staleness alert.
DRAMA_BACKLOG_MIN = int(os.getenv("DRAMA_BACKLOG_MIN", "3"))

# Daily "TÓM TẮT AI HÔM NAY (N bài)" narrative report to Telegram. OFF by
# default (chủ kênh yêu cầu 07/2026): kênh Bé MC giờ nhận narrative text + video
# cho TỪNG video TikTok (send_tiktok_manual), nên bản tóm tắt 5 bài buổi sáng
# thành tin nhắn thừa. Bật lại = AI_NARRATIVE_REPORT_ENABLED=1 — narrative vẫn
# được SINH ra như cũ (video cần nó), chỉ bước GỬI Telegram bị tắt.
AI_NARRATIVE_REPORT_ENABLED = os.getenv("AI_NARRATIVE_REPORT_ENABLED", "0") == "1"

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
