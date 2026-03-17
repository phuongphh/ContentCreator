import os
from dotenv import load_dotenv

load_dotenv()

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

# Limits
MAX_ARTICLES_PER_RUN = 50
MAX_DEEP_ANALYSIS = 10           # Tăng từ 5 → 10 để có đủ bài cho resume top 5
TOP_RESUME_COUNT = 5             # Số bài tối đa trong bản resume gửi Telegram

# Database
DB_PATH = os.path.join(os.path.dirname(__file__), "storage", "content.db")

# --- Video Pipeline ---
TTS_API_URL = os.getenv("TTS_API_URL", "")          # Your TTS endpoint
TTS_API_KEY = os.getenv("TTS_API_KEY", "")           # Your TTS API key
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "default")  # Voice ID for TTS

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

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
