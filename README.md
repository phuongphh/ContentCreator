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
│   ├── reddit_collector.py        # Reddit JSON API (r/ChatGPT, r/artificial) — track AI
│   ├── reddit_drama_collector.py  # Reddit RSS+JSON (AITA, ProRevenge, ...) — track Drama
│   └── producthunt_collector.py   # Product Hunt GraphQL API
├── processors/
│   ├── rule_filter.py             # Lọc keyword, không tốn AI
│   ├── ai_scorer.py               # Chấm điểm 1-10 bằng Claude Haiku — track AI
│   ├── ai_analyzer.py             # Phân tích sâu bằng Claude Sonnet — track AI
│   ├── prompt_loader.py           # load_prompt()/render() theo version (Phase 3)
│   ├── drama_scorer.py            # Rubric 6 tiêu chí bằng Haiku — track Drama
│   ├── drama_rewriter.py          # Việt hoá story bằng Sonnet + validate_rewrite()
│   ├── drama_compiler.py          # Gom 3-5 story cùng theme → video long-form
│   └── ab_harness.py              # A/B test prompt version
├── prompts/drama/                 # scorer.v1.txt, rewriter.v1.txt, ...
├── storage/
│   ├── database.py                # SQLite CRUD (articles/videos)
│   ├── stories.py                 # CRUD cho bảng stories (track Drama)
│   ├── compiled_videos.py         # CRUD cho bảng compiled_videos
│   ├── ab_runs.py                 # CRUD cho bảng ab_runs
│   ├── collector_health.py        # Alert Telegram nếu collector im lặng >2 ngày
│   ├── migrate.py                 # Migration runner (up/down/status)
│   └── migrations/                # File SQL versioned
├── notifier/
│   ├── telegram_bot.py            # Bot chính: báo cáo + approve/reject + dispatch seed bot
│   └── seed_bot.py                # /seed_vn, /seed_url, /list_pending (feed Drama seed)
├── channels.py                    # Channel registry (multi-channel, xem bên dưới)
├── config.py                      # Cấu hình tập trung
├── main.py                        # Pipeline orchestrator
├── requirements.txt
└── .env                           # API keys (không commit)
```

## Multi-channel architecture

Từ Phase 1, pipeline hỗ trợ nhiều kênh thay vì 1 kênh AI duy nhất:

- **`channels.py`** là registry duy nhất cho mọi destination (`ai_youtube`,
  `drama_youtube`, `tiktok_main`). Dùng `get_channel(key)` để tra cứu.
- Mỗi bài viết/video mang thêm `track` (`ai` | `drama`, mặc định `ai`) và
  `destination` (khoá trong `channels.py`).
- Áp dụng schema mới bằng migration runner:

  ```bash
  cd content-pipeline
  python -m storage.migrate up       # áp dụng migration còn pending
  python -m storage.migrate status   # xem migration nào đã/chưa apply
  ```

- Chi tiết thiết kế: [`docs/current/phase-1-detailed.md`](docs/current/phase-1-detailed.md).
- Logic routing nội dung sang đúng channel để upload sẽ được nối dây ở Phase 5;
  Phase 1 chỉ đặt khung schema + registry.

## Drama Source Layer (Phase 2)

Tầng thu thập nguồn cho track Drama — chưa có logic chấm điểm/rewrite (Phase 3):

- **`collectors/reddit_drama_collector.py`** — cào 5 subreddit drama (AITA,
  AskReddit, relationship_advice, MaliciousCompliance, ProRevenge) qua RSS +
  JSON detail (score, NSFW, selftext), lọc theo ngưỡng upvote riêng từng sub.
  ```bash
  cd content-pipeline
  python -m collectors.reddit_drama_collector
  ```
- **`notifier/seed_bot.py`** — lệnh Telegram `/seed_vn` (feed tình huống lõi
  VN-original), `/seed_url` (paste link FB/TikTok), `/list_pending` (xem
  story đang chờ duyệt). Chạy trong CÙNG bot approve/reject hiện có
  (`python main.py --bot`) — không phải process riêng, để tránh 2 poller
  tranh nhau 1 bot token (409 Conflict).
- **`storage/collector_health.py`** — alert Telegram nếu 1 collector chưa
  chạy thành công quá 2 ngày: `python -m storage.collector_health`.
- Chi tiết thiết kế: [`docs/current/phase-2-detailed.md`](docs/current/phase-2-detailed.md).

## Drama Generation Layer (Phase 3)

Biến story đã chấm điểm (Phase 2) thành script tiếng Việt sẵn-render. Video
production thật (TTS/render) vẫn thuộc Phase 4:

- **`processors/drama_scorer.py`** — chấm rubric 6 tiêu chí bằng Haiku, loại
  ngay nếu không an toàn (`safe=0`) dù điểm tổng cao.
  ```bash
  cd content-pipeline
  python -m processors.drama_scorer
  ```
- **`processors/drama_rewriter.py`** — Việt hoá story bằng Sonnet (đổi tên/
  địa điểm sang VN, thêm bình luận góc nhìn Việt ≥20% thời lượng), validate
  lại bằng heuristic độc lập (word count, tên VN, không lẫn văn hoá Mỹ).
  ```bash
  python -m processors.drama_rewriter
  ```
- **`processors/drama_compiler.py`** — gộp 3-5 story cùng theme thành video
  long-form 8-15 phút (chạy weekly).
- **`processors/ab_harness.py`** — A/B test prompt version, gán version theo
  hash ổn định của story_id (không phải random) để nhất quán giữa các lần gọi.
- Prompt versioning: `PROMPT_VERSION` trong `.env` (mặc định `v1`), file
  prompt tại `prompts/drama/`. Quyết định version + lý do:
  [`docs/current/prompts-decisions.md`](docs/current/prompts-decisions.md).
- Chi tiết thiết kế: [`docs/current/phase-3-detailed.md`](docs/current/phase-3-detailed.md).

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

## Documentation

- 🎯 [Product Strategy](docs/current/strategy.md)
- 📚 [All Docs](docs/README.md)
- 🐛 [Issues](docs/issues/README.md) ([Active](docs/issues/active/INDEX.md) · [Closed](docs/issues/closed/INDEX.md))

## GitHub Actions Workflows

| Workflow | Trigger | Chức năng |
|----------|---------|-----------|
| `issue-lifecycle.yml` | Issue opened/edited/closed/reopened/labeled | Sync issue vào `docs/issues/active/` ↔ `docs/issues/closed/by-phase/{phase}/`, regenerate INDEX |
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
