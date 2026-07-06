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
│   ├── rss_collector.py         # Thu thập RSS feeds (The Rundown, Ben's Bites, VnExpress)
│   ├── twitter_collector.py     # Twitter API v2
│   ├── reddit_collector.py      # Reddit API (r/ChatGPT, r/artificial) — track AI
│   └── reddit_drama_collector.py # Reddit RSS+JSON (AITA, ProRevenge, ...) — track Drama (Phase 2)
├── processors/
│   ├── rule_filter.py          # Lọc keyword, không dùng AI
│   ├── ai_scorer.py            # Chấm điểm 1-10 bằng Claude Haiku (rẻ) — track AI
│   ├── ai_analyzer.py          # Phân tích sâu bằng Claude Sonnet (bài tốt) — track AI
│   ├── prompt_loader.py        # load_prompt()/render() — đọc prompts/{module}/{name}.{version}.txt (Phase 3)
│   ├── ai_usage.py             # Log token usage mỗi call Anthropic (Phase 3)
│   ├── ab_harness.py           # A/B test prompt version (deterministic theo story_id, Phase 3)
│   ├── drama_scorer.py         # Rubric 6 tiêu chí bằng Haiku — track Drama (Phase 3)
│   ├── drama_rewriter.py       # Việt hoá story bằng Sonnet + validate_rewrite() — track Drama (Phase 3)
│   └── drama_compiler.py       # Gom 3-5 story cùng theme thành video long-form (Phase 3)
├── prompts/
│   └── drama/                  # scorer.v1.txt, rewriter.v1.txt, theme_detect.v1.txt, longform.v1.txt
├── storage/
│   ├── database.py             # SQLite — CRUD cho articles/videos
│   ├── stories.py              # CRUD cho bảng stories (track Drama, Phase 2/3)
│   ├── compiled_videos.py      # CRUD cho bảng compiled_videos (Phase 3)
│   ├── ab_runs.py              # CRUD cho bảng ab_runs (Phase 3)
│   ├── collector_health.py     # Theo dõi last_success/alert nếu collector im lặng (Phase 2)
│   ├── migrate.py              # Migration runner (up/down/status)
│   └── migrations/             # File SQL versioned, vd 001_multi_track.sql
├── notifier/
│   ├── telegram_bot.py         # Bot chính: báo cáo sáng + approve/reject + dispatch seed bot
│   └── seed_bot.py             # Command handlers /seed_vn, /seed_url, /list_pending (Phase 2)
├── channels.py                 # Channel registry — nguồn sự thật cho mọi destination
├── config.py                   # API keys, keywords, thresholds
├── main.py                     # Orchestrator — chạy toàn bộ pipeline
├── requirements.txt
└── .env                        # API keys (không commit lên git)
```

---

## Multi-channel architecture (Phase 1)

Từ Phase 1, pipeline hỗ trợ nhiều kênh/nhiều track thay vì 1 kênh AI duy nhất
(xem `docs/current/phase-1-detailed.md`).

- **`channels.py`** là channel registry — nguồn sự thật duy nhất cho mọi
  destination. `CHANNELS` có 3 entry: `ai_youtube`, `drama_youtube`,
  `tiktok_main`. Dùng `get_channel(key)` để tra cứu (raise `ValueError` nếu
  key sai). Module khác (uploader, scheduler) nên import từ đây thay vì
  hard-code tên kênh — logic routing thực tế (chọn đúng channel để upload)
  sẽ được nối dây ở Phase 5.
- Mọi bài viết (`articles`) và video (`videos`) mang thêm 2 cột:
  `track` (`'ai'` | `'drama'`, mặc định `'ai'` để không phá logic cũ) và
  `destination` (khoá trong `channels.py`, `NULL` nếu chưa quyết định).
- Bảng mới `stories` chuẩn bị cho track Drama (Phase 2): nội dung thô/rewritten,
  `rubric_score`, `status`, `destination`.
- Migration được áp dụng qua `python -m storage.migrate up` (chạy từ thư mục
  `content-pipeline/`), không phải qua `init_db()` — xem
  `storage/migrations/001_multi_track.sql`.

---

## Drama Source Layer (Phase 2)

Xem `docs/current/phase-2-detailed.md`. Xây tầng thu thập nguồn cho track
Drama — chưa có logic chấm điểm/rewrite (Phase 3).

- **`collectors/reddit_drama_collector.py`** — cào top post từ 5 subreddit
  (`AmItheAsshole`, `AskReddit`, `relationship_advice`, `MaliciousCompliance`,
  `ProRevenge`) qua RSS (`/top/.rss?t=day`), sau đó gọi JSON API
  (`/comments/{id}.json`) để lấy `score`/`selftext`/`over_18` — RSS không có
  các trường này. **Khác với tài liệu thiết kế:** lọc NSFW dùng cờ `over_18`
  chính thức từ JSON detail (không đoán từ RSS, vì Reddit không document rõ
  field NSFW trong RSS) — đằng nào cũng phải gọi JSON API nên dùng luôn nguồn
  đáng tin cậy hơn. Rate limit 1 req/2s, retry 3 lần backoff cho JSON call.
  Chạy: `python -m collectors.reddit_drama_collector` (06:06 sáng, xem
  `launchd/com.ai5phut.reddit-drama.plist`).
- **`storage/stories.py`** — CRUD cho bảng `stories`: `insert_story` (raise
  `sqlite3.IntegrityError` nếu `source_id` trùng — unique index từ migration
  002), `dedupe_check`, `get_pending(limit, track)`, `update_status` (chỉ
  nhận field trong allowlist, tránh dựng UPDATE động từ tên cột tuỳ ý).
- **`notifier/seed_bot.py`** — xử lý lệnh `/seed_vn`, `/seed_url`,
  `/list_pending` cho việc feed "tình huống lõi" VN-original thủ công.
  **Khác với tài liệu thiết kế:** tài liệu đề xuất chạy 1 process
  `python-telegram-bot` độc lập song song với bot approve/reject hiện có.
  Thực tế `seed_bot.py` chỉ export các hàm xử lý THUẦN, được
  `notifier/telegram_bot.py._handle_update()` gọi vào TRONG CÙNG vòng
  long-polling đang chạy — vì Telegram chỉ cho phép 1 `getUpdates` connection
  tại 1 thời điểm/bot token; 2 process độc lập cùng token sẽ liên tục bị lỗi
  409 Conflict. State hội thoại (chờ nội dung sau `/seed_vn`) lưu trong
  `notifier/.seed_state.json` (persisted qua restart).
- **`storage/collector_health.py`** — `record_success(name)` sau mỗi lần
  `reddit_drama_collector` chạy xong (không raise, kể cả 0 story mới — đó là
  bình thường, không phải lỗi). Job riêng `python -m storage.collector_health`
  (chạy 06:30 + 18:30, xem `launchd/com.ai5phut.drama-health.plist`) alert
  Telegram (`notifier.telegram_bot.send_alert`) nếu 1 collector chưa thành
  công quá 2 ngày — bắt lỗi cron dừng chạy hoặc crash không bắt được, không
  phải để phát hiện "0 bài hôm nay".
- Migration 002 (`stories.title`/`metadata` + unique `source_id`) và 003
  (`collector_health`) — chạy `python -m storage.migrate up` sau khi pull.

---

## Drama Generation Layer (Phase 3)

Xem `docs/current/phase-3-detailed.md`. Biến story `stories.status='pending'`
(đã qua Phase 2 + đã chấm điểm) thành script tiếng Việt sẵn-render. Ngoài
phạm vi: TTS/render video thật (Phase 4).

- **`processors/prompt_loader.py`** — `load_prompt(module, name, version=None)`
  đọc `prompts/{module}/{name}.{version}.txt`; `version` mặc định lấy từ
  `config.PROMPT_VERSION` (đổi env var để rollback, không cần sửa code).
  `render(template, **values)` điền placeholder `{{KEY}}` bằng `str.replace`
  (không phải `.format()`) — tránh phải escape dấu `{`/`}` thật trong các
  ví dụ JSON schema nằm ngay trong prompt.
- **`processors/drama_scorer.py`** — chấm 6 tiêu chí (HOOK_3S, STAKES, TWIST,
  LOCALIZABLE, COMMENT_BAIT, SAFE) bằng Haiku. `total` LUÔN được tính lại từ
  6 field boolean phía server — không tin số `total` model tự báo cáo (LLM
  occasionally tính sai tổng). `safe=0` luôn bị loại (`status='rejected'`) dù
  `total` cao. Story đạt ngưỡng (`config.DRAMA_SCORE_THRESHOLD`, mặc định 5/6)
  giữ nguyên `status='pending'`, sẵn sàng cho rewriter.
- **`processors/drama_rewriter.py`** — module quan trọng nhất: Việt hoá story
  bằng Sonnet (đổi tên/địa điểm sang VN, thêm `vn_commentary` ≥20% thời
  lượng). `validate_rewrite()` là heuristic gate độc lập với prompt (word
  count 800-1200, `vn_commentary` ≥200 từ, hook ngắn — proxy cho cấu trúc
  "Hook 3s", chặn tên/từ văn hoá Mỹ lọt qua). Rewrite hợp lệ →
  `status='approved'`; không hợp lệ → `status='needs_review'` + alert
  Telegram (nhưng output vẫn được lưu để người xem lại, không bị huỷ).
  **Cải tiến so với tài liệu gốc:** 2 rule phòng rủi ro mà tài liệu liệt kê
  ở mục "Rủi ro" (tên thuần Việt 2-3 từ, không nhắc văn hoá Mỹ) được đưa
  thẳng vào prompt v1 luôn, không đợi tune sang v2 — xem
  `docs/current/prompts-decisions.md`.
- **`processors/drama_compiler.py`** — gom 3-5 story cùng theme (`status=
  'produced'`, đã qua Phase 4) thành 1 script long-form 8-15 phút + chapter
  markers. `detect_theme()` (Sonnet, weekly) tìm theme xuất hiện ≥3 story;
  `compile_long_form()` sinh intro/bridge/outro + validate format chapter
  marker + word count (1100-2100 từ, suy ra từ tốc độ đọc ~140 từ/phút mà
  chính codebase này dùng cho video AI dài — xem `video/script_generator.py`).
- **`processors/ab_harness.py`** — A/B test prompt version. **Thiết kế rút
  gọn so với tài liệu:** `choose_version(experiment, story_id)` là hash
  ổn định (không phải random thật) để cùng 1 story luôn ra cùng 1 version
  dù gọi lại nhiều lần hay retry. `compare_ab_results()` so mean
  heuristic_score mỗi version sau khi đủ mẫu (`ab_runs` — migration 005).
- Migration 004 (`compiled_videos`) và 005 (`ab_runs`) — chạy
  `python -m storage.migrate up` sau khi pull.
- Prompt versioning + quyết định v1: `docs/current/prompts-decisions.md`.

---

## Drama Video Production Layer (Phase 4)

Xem `docs/current/phase-4-detailed.md`/`phase-4-issues.md`. Biến story
`status='approved'` (đã Việt hoá — Phase 3) thành video Shorts thật (audio +
hình + phụ đề). Ngoài phạm vi: orchestration nối vào `main.py` (chọn story,
gọi TTS, gọi `compose_drama_video`) — để dành cho bước wiring sau.

- **`video/tts/`** (per-track voice) — thay vì xây lại abstraction TTS mới
  như tài liệu đề xuất (EPIC #4.1: `ElevenLabsProvider`/`FPTAIProvider`),
  package `video/tts/` (base/factory/nuitruc/edge) đã có sẵn và đủ tốt —
  chỉ thêm tham số `voice_id` xuyên suốt chain (`TTSProvider.synthesize`,
  `factory.synthesize`, `tts_client.text_to_speech`). Hàm mới
  `tts_client.synthesize_for_track(text, track, output_path)` tra
  `config.TTS_VOICE_ID_AI`/`TTS_VOICE_ID_DRAMA` (mặc định rỗng → dùng voice
  mặc định của provider, không ép phải cấu hình).
- **`video/lower_third.py`** — `render_lower_third(name, role, ...)` render
  PNG tên/vai trò nhân vật (Pillow), overlay bằng ffmpeg `overlay` filter.
  **`video/commentary_card.py`** — `render_commentary_card(text, ...)` render
  thẻ bình luận (`vn_commentary`) dạng card nền tối bo góc mờ. Cả hai trả
  `None` nếu input rỗng — không ép caller phải luôn có overlay.
- **`video/image_generator.py`** — minh hoạ AI qua Replicate
  (`REPLICATE_API_TOKEN`/`REPLICATE_MODEL_VERSION`, cả hai mặc định rỗng —
  không tự bịa model/preset). Cache theo hash prompt tại
  `video/assets/illustrations/cache/`. Thiếu token/model/API lỗi → trả
  `None`, KHÔNG làm hỏng cả video (composer tự fallback sang gradient).
- **`video/templates/`** — `load_template(track, format)` tra bảng scene cố
  định (`drama.py`/`ai.py`). Track AI vẫn dùng composer đơn-nền cũ
  (template chỉ để tài liệu hoá scene shape, xem docstring `ai.py`) — chỉ
  track Drama thật sự render multi-scene. **Sửa lỗi tài liệu:** per-scene
  duration của `phase-4-detailed.md` cộng lại ra 90s nhưng doc ghi
  `duration_target: 75` — giữ tổng 90 (khớp các duration cụ thể có lý do rõ
  ràng: "Hook 3s", "Twist 25s"...) thay vì đoán scene nào sai, tương tự lỗi
  word-count Phase 3 (`docs/current/prompts-decisions.md`).
- **`video/drama_composer.py`** — `compose_drama_video()`: pre-render mỗi
  scene thành 1 đoạn ffmpeg riêng (nền + overlay lower-third/commentary nếu
  có), nối (`concat` demuxer, `-c copy`) thành 1 "scene reel", rồi đưa reel
  đó làm `bg_video` cho `compose_video()` **đã có sẵn** (video_composer.py)
  để tái dùng pipeline audio/phụ đề/crop đã ổn định — không viết lại logic
  đó. Scene reel lỗi → fallback về compose đơn-nền cũ (không bao giờ ra
  video rỗng). Nhạc nền: nếu `ENABLE_BGM=1`, mix nhạc từ
  `config.DRAMA_MUSIC_DIR` (pool riêng, khác `MUSIC_DIR` của track AI) —
  ưu tiên file tên trùng `template["music_track"]`, thiếu thì chọn ngẫu
  nhiên trong pool (không chặn render nếu chưa có file nhạc, xem
  `video/assets/music_drama/CREDITS.md` — cần thêm nhạc thủ công, giống
  bước branding thủ công ở Phase 1).
  **Khoảng trống đã biết:** `drama_rewriter.py` (Phase 3) chưa có field
  tên/vai trò nhân vật có cấu trúc, nên `lower_third`/`vn_commentary` là
  tham số optional caller tự truyền vào (không đoán parse từ `script` tự
  do) — bước gọi thực tế (orchestration) cần cung cấp dữ liệu này.
- Không có migration DB mới ở Phase 4 (chỉ thêm code + asset directories).

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
    used_at TIMESTAMP,
    -- Thêm ở migration 001_multi_track (storage/migrations/):
    track TEXT NOT NULL DEFAULT 'ai',   -- 'ai' | 'drama'
    destination TEXT                    -- khoá trong channels.py, NULL = chưa quyết định
);
```

`videos` (đã tồn tại từ Video Pipeline, xem `storage/database.py`) có cùng 2 cột
`track`/`destination` thêm bởi migration 001. Bảng `stories` (mới, chuẩn bị cho
track Drama — Phase 2) xem `storage/migrations/001_multi_track.sql`.

Migration chạy qua `python -m storage.migrate up` (idempotent, tracked bởi bảng
`_migrations`) — không chạy tự động trong `init_db()`.

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

## Video engine flags (Video Enhancement roadmap)

Các flag bật/tắt nâng cấp video, mặc định = hành vi cũ (xem
`docs/current/video-enhancement/`):

| Flag (env) | Default | Ý nghĩa |
|------------|---------|---------|
| `SUBTITLE_TIMING_MODE` | `wordcount` | `wordcount` (cũ) \| `whisper` (P1, bám audio) |
| `BACKGROUND_MODE` | `single` | `single` (cũ) \| `multi` (P1, nhiều clip) |
| `BG_VARIETY_TOPK` | `3` | Chọn ngẫu nhiên trong N clip khớp thời lượng nhất (chống nhàm). `1` = chọn cố định như cũ |
| `BG_RECENT_WINDOW` | `8` | Số clip nền vừa dùng cần tránh lặp lại giữa các video |
| `TTS_PROVIDER` | `nuitruc` | `nuitruc` (cũ) \| `edge` (P2) |
| `COMPOSER_ENGINE` | `ffmpeg` | `ffmpeg` (default) \| `moviepy` (P2) |
| `ENABLE_BGM` | `0` | `1` để trộn nhạc nền (P1) |
| `BURN_SUBTITLES` | `all` | `all` \| `short_only` (chỉ nung sub cho short, long upload caption track) \| `none` |
| `TTS_TIMEOUT` | `120` | Socket timeout khi tải `/result` (giây). Timeout = **fail fast**, không retry (issue #58) |
| `TTS_MAX_RETRIES` | `3` | Số retry cho lỗi HTTP transient nhanh (429/5xx). **Không** áp dụng cho timeout |
| `TTS_RETRY_DELAY` | `5` | Backoff ban đầu giữa các retry (giây), exponential |
| `TTS_REQUEST_TIMEOUT` | `30` | Socket timeout cho `/submit` và mỗi lần poll `/status` (giây) |
| `TTS_POLL_INTERVAL` | `12` | Khoảng cách giữa các lần poll `/status` (giây) |
| `TTS_POLL_TIMEOUT` | `600` | Tổng thời gian chờ tối đa 1 job; quá hạn → fallback provider |
| `TTS_POLL_MAX_FAILURES` | `3` | Số lần poll `/status` lỗi liên tiếp trước khi fallback (fail fast) |
| `TTS_ALLOW_INSECURE_SSL` | `0` | **Security:** chỉ bật cho endpoint TLS tự ký tin cậy; mặc định verify cert |

**Phụ đề theo định dạng:** với `BURN_SUBTITLES=short_only`, video **short** được nung
phụ đề (xem tắt tiếng trên TikTok/Shorts), còn video **long** không nung mà upload
SRT làm **caption track** lên YouTube (`captions.insert`, người xem bật/tắt). Nên
dùng kèm `SUBTITLE_TIMING_MODE=whisper` để timing bám audio.

**TTS resilience (issue #58):** khi endpoint primary (nuitruc) treo/không phản hồi,
client fail nhanh (không retry timeout) để fallback chain trong `video.tts.factory`
chuyển sang `edge` ngay — pipeline vẫn ra video thay vì block ~20 phút rồi hỏng.
Vì vậy `edge-tts` được cài mặc định (xem `requirements.txt`) làm provider dự phòng.

**TTS async job (script dài):** nuitruc dùng API bất đồng bộ thay cho `/api/tts`
đồng bộ (vốn timeout với script dài): `POST {base}/submit` → poll
`GET {base}/status/<job_id>` mỗi `TTS_POLL_INTERVAL`s đến khi `done`/`error` →
tải `GET {base}/result/<job_id>` **một lần** (gọi lần 2 ra 404 vì job đã bị xoá —
bình thường). Toàn bộ bị chặn bởi `TTS_REQUEST_TIMEOUT` / `TTS_POLL_TIMEOUT` /
`TTS_POLL_MAX_FAILURES` để job treo vẫn fail fast sang `edge` (giữ tinh thần #58).
Các endpoint con suy ra từ `TTS_API_URL` nên không cần đổi config URL.

`config.validate_flags(logger)` cảnh báo nếu giá trị không hợp lệ và pipeline tự
fallback về hành vi cũ.

**Composer:** phụ đề được gộp thành **một track trong suốt** (concat-demuxer →
overlay 1 lần) nên số input ffmpeg là hằng số, không phụ thuộc số dòng phụ đề.

**Chất lượng nền (background):** ba lớp cải tiến để nền bám nội dung và đỡ nhàm,
mà vẫn dùng nguồn Pexels miễn phí:
1. **B-roll terms theo nội dung:** `script_generator` để LLM trả thêm trường
   `broll_terms` (3-5 cụm tiếng Anh cụ thể, vd `"AI robot assistant"`). `main._extract_keywords`
   ưu tiên dùng các từ này để search Pexels (bám chủ đề hơn tiêu đề tiếng Việt);
   thiếu thì fallback heuristic cũ.
2. **Chống lặp nền (anti-repeat):** `pexels_downloader._select_with_variety` chọn
   ngẫu nhiên trong `BG_VARIETY_TOPK` clip khớp thời lượng nhất và tránh
   `BG_RECENT_WINDOW` clip vừa dùng (lưu ở `cache/.recent_backgrounds.json`) — fix
   việc mọi short ~60s đều ra cùng một nền do duration-match tất định.
3. **Crop-to-fill cho short:** video **short** (dọc) scale-up + center-crop cho
   đầy khung (`_scale_filter(fill=True)`) thay vì letterbox viền đen; video **long**
   giữ pad như cũ. (Engine MoviePy P2 chưa đồng bộ crop này — dùng `.resized()`.)

---

## Nguyên tắc khi viết code

- Dùng Python 3.10+
- Mỗi module độc lập, có thể chạy riêng lẻ để test
- Log đầy đủ mỗi bước (dùng Python `logging` module)
- Xử lý lỗi gracefully — nếu một nguồn bị lỗi, pipeline vẫn tiếp tục với các nguồn còn lại
- Tránh duplicate: kiểm tra URL đã có trong DB trước khi insert
- Không hardcode API keys — luôn dùng biến môi trường từ `.env`
