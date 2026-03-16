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
    # Vietnamese tech news
    "https://vnexpress.net/rss/khoa-hoc-cong-nghe.rss",      # VnExpress Công nghệ
    # Reddit
    "https://www.reddit.com/r/ChatGPT/.rss",                 # Reddit r/ChatGPT
    "https://www.reddit.com/r/artificial/.rss",               # Reddit r/artificial
]

# Twitter accounts to follow
TWITTER_ACCOUNTS = ["OpenAI", "AnthropicAI", "GoogleDeepMind", "sama", "levelsio"]

# Scoring thresholds
SCORE_THRESHOLD_ANALYSIS = 6.5
SCORE_THRESHOLD_NOTIFY = 7.0

# Limits
MAX_ARTICLES_PER_RUN = 50
MAX_DEEP_ANALYSIS = 5

# Database
DB_PATH = os.path.join(os.path.dirname(__file__), "storage", "content.db")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
