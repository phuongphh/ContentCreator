# AI 5 Phút Mỗi Ngày — Content Pipeline

Hệ thống tự động research và lọc nội dung AI hàng ngày cho kênh YouTube/TikTok tiếng Việt.

**Đối tượng:** Người Việt đi làm văn phòng (22–35 tuổi, không rành kỹ thuật)
**Mục tiêu:** Giúp họ hiểu và dùng AI trong 5 phút mỗi ngày
**Chi phí:** ~$4/tháng

## Pipeline Flow

```
Thu thập (RSS, Twitter, Reddit, Product Hunt)
    ↓
Lọc rule-based (keyword matching, miễn phí)
    ↓
Chấm điểm AI (Claude Haiku, rẻ)
    ↓
Phân tích sâu (Claude Sonnet, top 5 bài/ngày)
    ↓
Báo cáo Telegram (7:30 sáng mỗi ngày)
```

## Cấu trúc thư mục

```
content-pipeline/
├── collectors/
│   ├── rss_collector.py           # RSS feeds (The Rundown, Ben's Bites, VnExpress, Reddit)
│   ├── twitter_collector.py       # Twitter API v2 (OpenAI, Anthropic, Google, ...)
│   ├── reddit_collector.py        # Reddit JSON API (r/ChatGPT, r/artificial)
│   └── producthunt_collector.py   # Product Hunt GraphQL API
├── processors/
│   ├── rule_filter.py             # Lọc keyword, không tốn AI
│   ├── ai_scorer.py               # Chấm điểm 1-10 bằng Claude Haiku
│   └── ai_analyzer.py             # Phân tích sâu bằng Claude Sonnet
├── storage/
│   └── database.py                # SQLite CRUD
├── notifier/
│   └── telegram_bot.py            # Gửi báo cáo qua Telegram Bot API
├── config.py                      # Cấu hình tập trung
├── main.py                        # Pipeline orchestrator
├── requirements.txt
└── .env                           # API keys (không commit)
```

## Cài đặt

```bash
cd content-pipeline
cp .env.example .env
# Điền API keys vào .env

pip install -r requirements.txt
```

## Biến môi trường (.env)

| Biến | Mô tả | Bắt buộc |
|------|--------|----------|
| `ANTHROPIC_API_KEY` | API key Anthropic (Claude) | Có |
| `TELEGRAM_BOT_TOKEN` | Token của Telegram Bot | Có |
| `TELEGRAM_CHAT_ID` | Chat ID nhận báo cáo | Có |
| `TWITTER_BEARER_TOKEN` | Twitter API v2 Bearer Token | Không |
| `PRODUCTHUNT_API_TOKEN` | Product Hunt API Token | Không |

## Chạy pipeline

```bash
# Chạy toàn bộ pipeline
python main.py

# Hoặc chạy từng module riêng lẻ
python collectors/rss_collector.py
python processors/rule_filter.py
python processors/ai_scorer.py
python processors/ai_analyzer.py
python notifier/telegram_bot.py
```

## Cronjob

```bash
# Pipeline chính — 7:00 sáng mỗi ngày
0 7 * * * cd /path/to/content-pipeline && python main.py >> logs/pipeline.log 2>&1

# Thu thập Twitter thêm buổi trưa và tối
0 12 * * * cd /path/to/content-pipeline && python collectors/twitter_collector.py
0 20 * * * cd /path/to/content-pipeline && python collectors/twitter_collector.py
```

## Cấu hình ngưỡng

| Tham số | Giá trị | Mô tả |
|---------|---------|-------|
| `SCORE_THRESHOLD_ANALYSIS` | 6.5 | Bài >= điểm này mới phân tích sâu |
| `SCORE_THRESHOLD_NOTIFY` | 7.0 | Bài >= điểm này vào mục "đăng ngay" |
| `MAX_ARTICLES_PER_RUN` | 50 | Giới hạn bài thu thập mỗi lần |
| `MAX_DEEP_ANALYSIS` | 5 | Tối đa bài phân tích sâu/ngày |

## GitHub Actions Workflows

| Workflow | Trigger | Chức năng |
|----------|---------|-----------|
| `sync-issues.yml` | Issue opened/edited | Export issue thành markdown trong `docs/issues/` |
| `auto-pr.yml` | Push to `claude/**` | Tự tạo PR, link issue, chuyển status "In Progress" |
| `code-review.yml` | PR opened/updated | Code review tự động bằng Claude Haiku |
| `project-done.yml` | PR merged | Chuyển issue sang "Done" trên Project board |
| `deploy.yml` | Push to `main` | Deploy lên VPS qua SSH |

## GitHub Secrets cần cấu hình

| Secret | Dùng cho |
|--------|----------|
| `ANTHROPIC_API_KEY` | Code review workflow |
| `PAT_TOKEN` | Auto PR + Project board automation |
| `SSH_HOST` | Deploy workflow |
| `SSH_USER` | Deploy workflow |
| `SSH_PRIVATE_KEY` | Deploy workflow |

## Nguồn dữ liệu

- **RSS:** The Rundown AI, Ben's Bites, VnExpress Công nghệ, Reddit r/ChatGPT, r/artificial
- **Twitter:** @OpenAI, @AnthropicAI, @GoogleDeepMind, @sama, @levelsio
- **Product Hunt:** Top AI posts hàng ngày

## Yêu cầu hệ thống

- Python 3.10+
- SQLite (có sẵn trong Python)
