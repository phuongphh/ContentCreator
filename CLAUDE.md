# CLAUDE.md — Content Pipeline: "AI 5 Phút Mỗi Ngày"

## Mô tả dự án

Hệ thống tự động research và lọc nội dung AI hàng ngày cho kênh YouTube/TikTok
tiếng Việt. Kênh hướng đến người Việt đi làm văn phòng (22–35 tuổi, không rành
kỹ thuật), giúp họ hiểu và dùng AI trong 5 phút mỗi ngày.

## Mục tiêu hệ thống

Chạy tự động mỗi sáng để:
1. Thu thập tin tức AI từ nhiều nguồn
2. Lọc sơ bộ bằng rule-based (không tốn tiền AI)
3. Chấm điểm relevance bằng AI model rẻ (Haiku)
4. Phân tích sâu bài tốt bằng AI model mạnh hơn (Sonnet)
5. Gửi báo cáo tóm tắt qua Telegram mỗi sáng

Chi phí mục tiêu: ~$4/tháng

---

## Kiến trúc thư mục

```
content-pipeline/
├── collectors/
│   ├── rss_collector.py        # Thu thập RSS feeds (The Rundown, Ben's Bites, VnExpress)
│   ├── twitter_collector.py    # Twitter API v2
│   └── reddit_collector.py     # Reddit API (r/ChatGPT, r/artificial)
├── processors/
│   ├── rule_filter.py          # Lọc keyword, không dùng AI
│   ├── ai_scorer.py            # Chấm điểm 1-10 bằng Claude Haiku (rẻ)
│   └── ai_analyzer.py          # Phân tích sâu bằng Claude Sonnet (bài tốt)
├── storage/
│   └── database.py             # SQLite
├── notifier/
│   └── telegram_bot.py         # Gửi báo cáo sáng qua Telegram Bot API
├── config.py                   # API keys, keywords, thresholds
├── main.py                     # Orchestrator — chạy toàn bộ pipeline
├── requirements.txt
└── .env                        # API keys (không commit lên git)
```

---

## Nguồn dữ liệu cần thu thập

### RSS Feeds (dùng feedparser)
- `https://www.therundown.ai/feed` — Newsletter AI hàng ngày
- `https://bensbites.beehiiv.com/feed` — Ben's Bites newsletter
- `https://vnexpress.net/rss/khoa-hoc-cong-nghe.rss` — VnExpress Công nghệ
- `https://www.reddit.com/r/ChatGPT/.rss` — Reddit r/ChatGPT
- `https://www.reddit.com/r/artificial/.rss` — Reddit r/artificial

### Twitter/X (Twitter API v2)
Theo dõi tweets từ các account: `OpenAI`, `AnthropicAI`, `GoogleDeepMind`, `sama`, `levelsio`

### Product Hunt
- API endpoint: `https://api.producthunt.com/v2/api/graphql`
- Lấy top posts hàng ngày có tag `artificial-intelligence`

---

## Bộ lọc Rule-based (rule_filter.py)

### RELEVANT_KEYWORDS — bài CÓ chứa những từ này mới giữ lại
```python
RELEVANT_KEYWORDS = [
    # Tiếng Anh
    "chatgpt", "claude", "gemini", "gpt-4", "gpt-5", "llm",
    "ai tool", "ai feature", "productivity", "workflow", "automation",
    "prompt", "copilot", "midjourney", "sora", "runway",
    # Tiếng Việt
    "trí tuệ nhân tạo", "công cụ ai", "ai tạo sinh"
]
```

### SKIP_KEYWORDS — bài chứa những từ này thì bỏ qua ngay (quá kỹ thuật hoặc không phù hợp)
```python
SKIP_KEYWORDS = [
    "arxiv", "paper", "dataset", "benchmark", "fine-tuning",
    "huggingface", "github repo", "open source weights",
    "fundraising", "valuation", "lawsuit", "regulation", "policy",
    "acquisition", "merger", "ipo"
]
```

---

## AI Scoring — ai_scorer.py

**Model:** `claude-haiku-4-5` (rẻ nhất, đủ dùng cho tác vụ chấm điểm đơn giản)

Chỉ đưa vào: `title` + `summary` (tối đa 300 ký tự). Không đưa full content để tiết kiệm token.

**Prompt template:**
```
Bạn là content strategist cho kênh YouTube/TikTok về AI dành cho
người Việt đi làm văn phòng (22-35 tuổi, không rành kỹ thuật).
Định vị kênh: "Giúp người Việt đi làm hiểu và dùng AI trong 5 phút"

Chấm điểm bài viết sau từ 1-10 theo 4 tiêu chí:
1. Người đi làm bình thường có quan tâm không? (1-10)
2. Có thể làm theo/áp dụng ngay hôm nay không? (1-10)
3. Giải thích được trong 5 phút không? (1-10)
4. Có gây cảm xúc (tò mò/hữu ích/lo lắng) không? (1-10)

TIÊU ĐỀ: {title}
TÓM TẮT: {summary}

Trả lời CHỈ bằng JSON, không giải thích thêm:
{"score_1": <số>, "score_2": <số>, "score_3": <số>, "score_4": <số>, "total": <trung bình cộng>}
```

**Ngưỡng:** Bài có `total >= 6.5` mới được đưa sang bước phân tích sâu.

---

## AI Analysis — ai_analyzer.py

**Model:** `claude-sonnet-4-5` (mạnh hơn, dùng cho top ~5 bài/ngày)

Đưa vào full content bài viết.

**Prompt template:**
```
Bạn là chuyên gia content creator về AI tại Việt Nam.
Phân tích bài viết sau và tạo content brief cho video YouTube/TikTok.

BÀI VIẾT:
{full_content}

Tạo JSON với cấu trúc sau:
{
  "category": "tips|news|comparison",
  "urgency": "immediate|this_week|backlog",
  "hooks": ["hook 1", "hook 2", "hook 3"],
  "viet_angle": "Cách Việt hoá và liên hệ thực tế cho người đi làm VN",
  "youtube_titles": ["title 1", "title 2", "title 3"],
  "tiktok_hashtags": ["#tag1", "#tag2", ...],
  "production_difficulty": "easy|medium|hard",
  "difficulty_reason": "lý do ngắn gọn",
  "one_line_summary": "tóm tắt 1 câu bằng tiếng Việt"
}
```

---

## Database Schema (SQLite)

```sql
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT UNIQUE,
    raw_content TEXT,
    summary TEXT,
    ai_score REAL,
    ai_analysis TEXT,           -- JSON string từ ai_analyzer
    category TEXT,              -- 'tips', 'news', 'comparison'
    urgency TEXT,               -- 'immediate', 'this_week', 'backlog'
    status TEXT DEFAULT 'pending', -- 'pending', 'used', 'skipped'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP
);
```

---

## Telegram Notification (telegram_bot.py)

Gửi báo cáo mỗi sáng lúc 7:30 sau khi pipeline chạy xong (cronjob 7:00).

**Format tin nhắn:**
```
📊 BÁO CÁO CONTENT - {ngày hôm nay}

🔥 ĐĂNG NGAY ({n} bài):
1. [{score}/10] {title}
   → Góc: {viet_angle}
   → Loại: {category}
   → Link: {url}

📅 TRONG TUẦN ({n} bài):
...

💾 BACKLOG: {n} bài
```

---

## Cấu hình (config.py + .env)

**Biến môi trường cần có trong .env:**
```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TWITTER_BEARER_TOKEN=...
PRODUCTHUNT_API_TOKEN=...
```

**Cấu hình ngưỡng trong config.py:**
```python
SCORE_THRESHOLD_ANALYSIS = 6.5   # Bài >= điểm này mới phân tích sâu
SCORE_THRESHOLD_NOTIFY = 7.0     # Bài >= điểm này mới vào báo cáo "đăng ngay"
MAX_ARTICLES_PER_RUN = 50        # Giới hạn bài thu thập mỗi lần chạy
MAX_DEEP_ANALYSIS = 5            # Tối đa bài phân tích sâu mỗi ngày (kiểm soát chi phí)
```

---

## Cronjob Schedule

```bash
# Chạy pipeline lúc 7:00 sáng mỗi ngày
0 7 * * * cd /path/to/content-pipeline && python main.py >> logs/pipeline.log 2>&1

# Thu thập Twitter thêm buổi trưa và tối
0 12 * * * cd /path/to/content-pipeline && python collectors/twitter_collector.py
0 20 * * * cd /path/to/content-pipeline && python collectors/twitter_collector.py
```

---

## Thứ tự build (implement theo thứ tự này)

1. `config.py` + `.env.example` — setup cấu hình trước
2. `storage/database.py` — tạo DB và các hàm CRUD cơ bản
3. `collectors/rss_collector.py` — bắt đầu bằng RSS vì đơn giản nhất, không cần API key
4. `processors/rule_filter.py` — lọc sơ bộ, không cần AI
5. `processors/ai_scorer.py` — tích hợp Haiku để chấm điểm
6. `processors/ai_analyzer.py` — tích hợp Sonnet để phân tích sâu
7. `notifier/telegram_bot.py` — gửi báo cáo
8. `main.py` — kết nối tất cả thành pipeline hoàn chỉnh
9. `collectors/twitter_collector.py` — thêm sau khi pipeline cơ bản chạy được
10. `collectors/reddit_collector.py` — tương tự Twitter

---

## Nguyên tắc khi viết code

- Dùng Python 3.10+
- Mỗi module độc lập, có thể chạy riêng lẻ để test
- Log đầy đủ mỗi bước (dùng Python `logging` module)
- Xử lý lỗi gracefully — nếu một nguồn bị lỗi, pipeline vẫn tiếp tục với các nguồn còn lại
- Tránh duplicate: kiểm tra URL đã có trong DB trước khi insert
- Không hardcode API keys — luôn dùng biến môi trường từ `.env`
