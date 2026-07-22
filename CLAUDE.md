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
│   ├── reddit_client.py         # Shared Reddit HTTP (OAuth app-only + fallback) — issue #78
│   ├── reddit_collector.py      # Reddit JSON API (r/ChatGPT, r/artificial) — track AI
│   ├── reddit_drama_collector.py # Reddit JSON listing (AITA, ProRevenge, ...) — track Drama (Phase 2, #78)
│   ├── lemmy_drama_collector.py # Lemmy public API (open Reddit-alt) — track Drama (#78 follow-up)
│   ├── hf_drama_importer.py     # Bulk import HuggingFace AITA dataset → stories (#78 follow-up)
│   └── gsheet_drama_importer.py # Google Sheets bridge: Make.com RSS / dán tay → stories (track Drama)
├── analytics/
│   ├── youtube_puller.py       # Pull metric YouTube Analytics API v2 → video_metrics/channel_metrics (Phase 6)
│   ├── tiktok_csv.py           # Parse CSV TikTok Studio → video_metrics (Phase 6)
│   ├── experiment_compare.py   # compare_arms() A/B video theo metric thật + Welch t-test (Phase 6)
│   ├── stats.py                # Welch t-test + p-value, KHÔNG cần scipy (Phase 6)
│   ├── pricing.py              # Overlay token → USD (đổi giá không đụng dữ liệu) (Phase 6)
│   └── weekly_retro.py         # Báo cáo tuần Telegram: top/bottom/sub growth/cost/action (Phase 6)
├── dashboard/
│   ├── data.py                 # Tầng dữ liệu KPI thuần (test được, không cần streamlit) (Phase 6)
│   └── app.py                  # Streamlit dashboard 4 tab (Overview/Top/Format/Cost) (Phase 6)
├── processors/
│   ├── rule_filter.py          # Lọc keyword, không dùng AI
│   ├── ai_scorer.py            # Chấm điểm 1-10 bằng Claude Haiku (rẻ) — track AI
│   ├── ai_analyzer.py          # Phân tích sâu bằng Claude Sonnet (bài tốt) — track AI
│   ├── prompt_loader.py        # load_prompt()/render() — đọc prompts/{module}/{name}.{version}.txt (Phase 3)
│   ├── ai_usage.py             # Log + persist token usage mỗi call Anthropic vào cost_logs (Phase 3/6)
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
│   ├── scheduled_posts.py      # CRUD queue upload theo cadence + atomic claim (Phase 5)
│   ├── quota.py                # Track unit YouTube API/ngày (reset giờ Pacific) + alert 80% (Phase 5)
│   ├── video_metrics.py        # CRUD snapshot số liệu video (upsert theo ngày) (Phase 6)
│   ├── channel_metrics.py      # CRUD snapshot cấp kênh (sub growth cho retro) (Phase 6)
│   ├── cost_logs.py            # CRUD token/chi phí mỗi call AI (Phase 6)
│   ├── migrate.py              # Migration runner (up/down/status)
│   └── migrations/             # File SQL versioned, vd 001_multi_track.sql
├── notifier/
│   ├── telegram_bot.py         # Bot chính: báo cáo sáng + approve/reject + dispatch seed/review bot
│   ├── seed_bot.py             # Command handlers /seed_vn, /seed_url, /list_pending (Phase 2)
│   ├── review_bot.py           # Review gate: nút ✅/❌/✏️ + FSM edit metadata (Phase 5)
│   └── analytics_bot.py        # Command /import_tiktok_csv + handler nạp CSV (Phase 6)
├── publisher/
│   ├── youtube_uploader.py     # Upload YouTube; upload_to_youtube(video_id, channel_key) multi-channel (Phase 5)
│   ├── tiktok_uploader.py      # TikTok Content Posting API
│   ├── tiktok_manual.py        # Export queue_tiktok/YYYY-MM-DD/ cho upload tay (Phase 5)
│   ├── token_health.py         # Giám sát OAuth token MỌI kênh YouTube + alert Telegram (#94)
│   └── scheduler.py            # Lịch NGÀY nào tạo video loại gì (short T2-T7, long CN)
├── scheduler/
│   └── post_scheduler.py       # Queue GIỜ đăng theo cadence + tick 5 phút (Phase 5)
├── webui/
│   ├── app.py                  # Streamlit approve/reject UI (local-only)
│   └── health.py               # GET /health — trạng thái module (Phase 5)
├── channels.py                 # Channel registry — nguồn sự thật cho mọi destination
├── config.py                   # API keys, keywords, thresholds
├── main.py                     # Orchestrator track AI — chạy toàn bộ pipeline
├── main_drama.py               # Orchestrator track Drama end-to-end (Phase 5)
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
  đã được nối dây ở Phase 5 (`publisher/youtube_uploader.upload_to_youtube`,
  `scheduler/post_scheduler.py`, `notifier/review_bot._destinations_for`).
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

- **`collectors/reddit_client.py`** — single source of truth cho MỌI truy cập
  Reddit (issue #78), cả track AI lẫn Drama đều đi qua đây. **Root cause #78:**
  Reddit chặn quyết liệt request KHÔNG xác thực tới `www.reddit.com/*.json` +
  `.rss` (403/429), nhất là từ IP datacenter; User-Agent generic (`example.com`)
  càng dễ bị chặn. Fix đúng gốc = OAuth2. `get_json(path, params)` dùng OAuth
  app-only (`client_credentials` grant → `oauth.reddit.com`, ~100 req/phút) khi
  có `REDDIT_CLIENT_ID`/`SECRET`; chưa cấu hình thì fallback
  `www.reddit.com/<path>.json` (best-effort, cảnh báo 1 lần). Token cache tới
  ~1 phút trước hạn; rate-limit chung 1 req/`REDDIT_MIN_INTERVAL`s cho cả token
  lẫn data. Xử lý lỗi: **429 tôn trọng header `Retry-After`** (cap
  `REDDIT_RETRY_AFTER_CAP` để giá trị điên không treo cron), **403 = chặn cứng,
  KHÔNG retry** (retry một block chỉ phí cả cửa sổ cron), 401 refresh token 1
  lần, 5xx/mạng backoff + retry. Chỉ dùng stdlib (urllib) — không thêm dependency.
  **Reddit TẮT mặc định (follow-up #78):** tháng 11/2025 Reddit khai tử việc tạo
  app tự phục vụ (Responsible Builder Policy) — cài mới không lấy được OAuth
  credentials nếu không qua duyệt tay nhiều tuần (hay bị từ chối dự án cá nhân).
  Nên `collection_enabled()` = `config.REDDIT_ENABLED` **và** có creds; mặc định
  `REDDIT_ENABLED=0` → collector KHÔNG chạm Reddit (tránh nã endpoint public làm
  re-flag IP). Có creds đã duyệt → đặt `REDDIT_ENABLED=1`, code resume nguyên
  vẹn qua OAuth. Cả 2 collector (`collect_all_reddit`/`collect_all_drama`)
  early-return 0 khi tắt.
- **`collectors/reddit_drama_collector.py`** — cào top post từ 5 subreddit
  (`AmItheAsshole`, `AskReddit`, `relationship_advice`, `MaliciousCompliance`,
  `ProRevenge`) qua JSON listing `/r/{sub}/top?t=day` (một request đã mang đủ
  `score`/`selftext`/`over_18`). Lọc NSFW bằng cờ `over_18` chính thức, bỏ post
  `stickied` (megathread/thông báo) và selftext `[removed]`/`[deleted]`.
  **Đổi kiến trúc ở #78:** bản Phase 2 dùng RSS rồi gọi thêm 1 JSON detail cho
  TỪNG post (pattern 1-RSS-cộng-N-detail: chậm ~2s/detail, dễ 403/429) — nay
  chuyển hẳn sang JSON listing qua `reddit_client`, bỏ được cả RSS (hết phụ
  thuộc `feedparser` ở collector này) lẫn vòng N detail call → nhanh hơn nhiều
  và ít bị chặn. 403 khiến `get_json` trả `None`; `fetch_subreddit_top` biến
  cái đó thành `RedditFetchError` (phân biệt với "fetch xong nhưng rỗng"). Nếu
  MỌI subreddit fail → `collect_all_drama` raise → `__main__` KHÔNG gọi
  `record_success` → alert staleness 2 ngày mới bắt được. (Không raise thì
  block kéo dài vẫn refresh `last_success` mỗi ngày → alert không bao giờ fire —
  lỗi Codex bắt ở PR #79.) **Khi Reddit tắt (mặc định, follow-up #78):**
  `collect_all_drama` early-return 0, `__main__` KHÔNG ghi `record_success` (không
  có collector sống để "healthy"); nguồn Drama chuyển sang seed thủ công
  (`seed_bot`), sức khoẻ theo dõi bằng backlog alert (xem dưới). Chạy:
  `python -m collectors.reddit_drama_collector` (06:06 sáng, xem
  `launchd/com.ai5phut.reddit-drama.plist`).
- **`collectors/lemmy_drama_collector.py`** — nguồn thay Reddit (follow-up #78).
  Reddit khoá tạo app tự phục vụ (11/2025) nên Drama chuyển sang **Lemmy** —
  Reddit-alternative liên hợp (fediverse), **API đọc công khai KHÔNG cần OAuth/
  duyệt/key**. Lấy top-of-day qua `GET {instance}/api/v3/post/list?community_
  name=...&sort=TopDay` (một request/community, cùng shape parse như reddit
  drama). Lọc `nsfw`/`featured_*` (stickied)/`removed`/score < `LEMMY_MIN_SCORE`;
  `source_id` hash từ `ap_id` (dedupe xuyên instance). Story tiếng Anh → được
  `drama_rewriter` Việt hoá như cũ. Bật mặc định (`LEMMY_ENABLED=1`); mặc định
  cào 2 community body-story `relationship_advice`/`aita`@lemmy.world, cấu
  hình qua `LEMMY_COMMUNITIES` ("name@instance", phẩy); community 404 chỉ log +
  bỏ qua (không fatal). Total outage (MỌI community fail) mới raise (như
  `collect_all_drama`). **Hai chế độ:** (1) *body-story* (mặc định, `relationship_
  advice`/`aita`) — body bài LÀ story; (2) *Q&A / AskReddit-style* (community
  trong `LEMMY_QA_COMMUNITIES`) — giá trị nằm ở COMMENTS, nên
  gọi thêm `/api/v3/comment/list?sort=Top&max_depth=1`, ghép "câu hỏi + top câu
  trả lời" thành story (`metadata.format='qa'`); lọc comment theo score/độ dài,
  cần ≥`LEMMY_QA_MIN_COMMENTS`, cap `_MAX_QA_POSTS_PER_RUN` post/community để giới
  hạn số request. Chỉ stdlib. **`asklemmy` bị GỠ khỏi default (issue #90):** nó
  là Q&A general (nhạc/thú cưng/PC), zero drama — 8/8 post cào từ đó bị
  `drama_scorer` loại 1-3/6, nã Haiku vào nội dung không bao giờ pass.
  `LEMMY_QA_COMMUNITIES` giờ mặc định **rỗng** (máy móc Q&A giữ nguyên, trỏ vào
  community drama thật thì bật lại). **Lemmy chỉ là topping TƯƠI best-effort** —
  lưu lượng drama thấp, nên nguồn drama tin cậy hàng ngày là dump AITA trên HF
  (xem dưới), KHÔNG phải Lemmy. Chạy vào bước collect của `main_drama`.
- **`collectors/hf_drama_importer.py`** — nạp story từ dataset AITA công khai trên
  HuggingFace qua **datasets-server REST API** (`/rows?dataset&config&split&offset&
  length`, stdlib — không cần lib `datasets`). Tự dò cột title/body (override
  `HF_TITLE_FIELD`/`HF_BODY_FIELD`), phân trang ≤100 dòng/request, `source_id` từ
  id dataset hoặc hash title+body (idempotent, re-run bỏ trùng). **Comment chất
  lượng (issue #92 follow-up):** với AITA thì phần phán xét cộng đồng (YTA/NTA +
  reply gắt) là "comment bait" mạnh nhất, nên nếu dataset có cột comment
  (`top_comments`/`comments`/… — tự dò, override `HF_COMMENTS_FIELD`) thì lọc theo
  score/độ dài (mirror bộ lọc Q&A của Lemmy: `HF_COMMENT_MIN_SCORE`/`_MIN_CHARS`,
  cap `HF_COMMENT_TOP_N`), rank theo score, gắn vào CUỐI `raw_content` dưới nhãn
  "TOP COMMENTS FROM REDDIT" (để scorer/rewriter — vốn đọc `raw_content[:4000]` —
  nhìn thấy) + lưu structured ở `metadata.top_comments`. Parse được cả 3 dạng dump
  (JSON list dict có score / JSON list string / một comment string thuần); comment
  KHÔNG lọt vào `source_id` (vẫn từ title+body) nên một dòng dedupe y hệt dù có/không
  comment. No-op êm khi dataset không có cột comment; tắt bằng `HF_IMPORT_COMMENTS=0`.
  Cả đường API lẫn CSV dùng chung `_import_row` nên enrichment nhất quán. **HF = nguồn
  drama TIN CẬY hàng ngày (issue #90):** Reddit tắt + Lemmy drama cạn nên dump
  AITA 270K dòng là giếng drama THẬT duy nhất đáng tin — và với kênh drama thì
  "thời sự" KHÔNG quan trọng (một vụ AITA năm 2019 hấp dẫn y như 2026; freshness
  là mối lo của track AI-news, không phải track này). Deep backfill dataset
  "tự cập nhật" đã chết vẫn không sao vì ta không cần chúng tươi. **Deep backfill
  (một lần):** `python -m collectors.hf_drama_importer --dataset
  OsamaBsher/AITA-Reddit-Dataset --limit 500` (270K bài, 2013–2023).
  **`import_daily()` — con trỏ tiến (mặc định của bước collect):** đi TỚI trong
  dump TĨNH mỗi ngày một lát `HF_DAILY_LIMIT` dòng chưa gặp, lưu offset ở
  `pipeline_state` (migration 008) nên hôm sau tiếp đúng chỗ dừng — KHÁC
  `--newest`, không bao giờ nạp lại cùng đuôi; hết dataset thì cuộn về 0 (270K
  dòng ~10/ngày = nhiều năm runway). Con trỏ advance theo số dòng ĐÃ QUÉT
  (không phải số import) nên bỏ-qua-rỗng không bị quét lại; `dedupe_check` vẫn
  gác từng dòng nên con trỏ và backfill tay `--limit N` sống chung an toàn; fetch
  lỗi thì con trỏ KHÔNG advance (lần sau retry cùng lát). **`--newest`
  (`import_dataset(newest=True)`):** lấy đuôi — CHỈ hợp dataset xác nhận còn
  append; với dump tĩnh nó re-poll đuôi cũ (nạp 0). Bật/tắt qua
  `HF_DRAMA_DAILY_ENABLED` (mặc định **1**), chọn chế độ qua `HF_DRAMA_DAILY_MODE`
  (`cursor` mặc định | `newest`). **Suy giảm êm (PA1, issue #90):** khi
  datasets-server không phục vụ được rows (viewer đang build/hỏng → body
  KHÔNG-JSON hay 5xx qua mọi retry) → `_fetch_rows` raise
  `HFDatasetUnavailableError` (phân biệt với 404 misconfig / lỗi code).
  **Raw-CSV fallback (issue #92):** datasets-server `/rows` hay 503 dài ngày với
  dataset lớn, làm nguồn drama hàng ngày cạn dù dataset vẫn ổn. Nay khi API báo
  `HFDatasetUnavailableError`, importer TỰ chuyển sang tải CSV thô của dataset qua
  Git LFS trên Hub (`huggingface.co/datasets/<ds>/resolve/main/<file>.csv`) — Hub
  vẫn sống trong các đợt viewer sập. CSV **cache 1 lần trên đĩa** (dump tĩnh nên
  không cần tải lại; `HF_CSV_CACHE_DIR`, cap `HF_CSV_MAX_BYTES` chống fill đĩa,
  `HF_CSV_CACHE_TTL_DAYS=0` = không hết hạn). **Consistency:** đọc CSV DÙNG CHUNG
  `_resolve_columns`/`_import_row`/`_row_source_id` với đường API, và CSV cùng
  THỨ TỰ dòng như datasets-server, nên con trỏ `import_daily` và dedupe `source_id`
  KHỚP y hệt khi API↔CSV đổi qua lại giữa chừng (một dòng nạp lối nào cũng ra cùng
  `source_id` → không nạp trùng). Tên CSV auto-dò qua Hub API (`/api/datasets/X`,
  vẫn sống khi viewer sập), override `HF_DRAMA_CSV_FILE`; không thấy `.csv` = lỗi
  cứng (surface). Bật/tắt `HF_CSV_FALLBACK_ENABLED` (mặc định **1**). Đường CSV
  thủ công: cờ `--csv` (vd `--dataset X --limit 500 --csv`) đọc thẳng CSV, bỏ qua
  API — đúng workaround của issue #92. Chỉ khi **CẢ API lẫn CSV** đều sập
  `import_daily` mới raise `HFDatasetUnavailableError`; bước collect `main_drama`
  coi đó là **cảnh báo mềm, KHÔNG nhét vào summary lỗi** (đỡ spam Telegram) — kho
  drama do **deep backfill một lần** gánh. Con trỏ KHÔNG advance khi outage (mai
  retry cùng lát). *License:* dataset tái phân phối nội dung Reddit — kiểm tra
  terms từng dataset.
- **`collectors/gsheet_drama_importer.py`** — cầu nối Google Sheets: phễu nạp
  chung cho mọi nguồn pipeline KHÔNG cào trực tiếp được (xem
  `docs/current/gsheet-drama-source.md`). Kiểm chứng 07/2026: Reddit RSS
  (`/hot/.rss`) vẫn tồn tại nhưng chặn fetcher datacenter TỪNG ĐỢT (Make.com/
  FreshRSS/RSS-Bridge đều dính 403 theo đợt); Quora topic RSS đã khai tử;
  Facebook không có RSS chính thức. Nên kiến trúc = tách tầng cào khỏi tầng
  nạp: **Make.com Free (1.000 ops/tháng, scenario RSS → Google Sheets "Add a
  Row") hoặc dán tay drama Việt** đổ vào MỘT sheet; importer đọc **CSV export**
  của sheet (share "Anyone with link – Viewer" hoặc Publish-to-web CSV; nhận cả
  link `/edit` thường — tự đổi sang `export?format=csv`) bằng stdlib urllib
  (UA `HTTP_USER_AGENT` #97, timeout + cap `GSHEET_MAX_BYTES`). Auto-dò cột
  Title/Content/URL/Source (header Anh/Việt có dấu — nhớ map đ→d khi strip
  accent), gỡ HTML (RSS content là HTML), dedupe `source_id` = hash URL (hoặc
  hash title+content khi dán tay), cap `GSHEET_IMPORT_LIMIT`/lần chạy (mặc
  định 30 — chống một cú dán lớn nã hết Haiku; phần dư tự vào lần sau). Sheet
  chưa share → lỗi rõ "got an HTML page instead of CSV". Rỗng
  `GSHEET_DRAMA_URL` = tắt (mặc định). Chạy trong bước collect `main_drama`
  (best-effort như Reddit/Lemmy/HF) hoặc tay:
  `python -m collectors.gsheet_drama_importer`.
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
- **`storage/collector_health.py`** — `record_success(name)`/`check_and_alert`
  (staleness generic) vẫn còn cho collector nào có nguồn sống. **Đổi ở
  follow-up #78:** vì Reddit tắt mặc định, job riêng `python -m
  storage.collector_health` (06:30 + 18:30, `launchd/com.ai5phut.drama-health.plist`)
  giờ gọi `check_drama_backlog()` thay cho `check_and_alert(["reddit_drama"])` —
  tín hiệu đúng khi sống bằng seed thủ công là "còn đủ story để sản xuất
  không", không phải "collector có chạy không". Alert Telegram khi
  `stories.count_producible("drama")` (pending + approved) < `DRAMA_BACKLOG_MIN`
  (mặc định 3), nhắc `/seed_vn`. Best-effort, không raise. Bật lại Reddit thì
  thêm `check_and_alert(["reddit_drama"])` song song.
- Migration 002 (`stories.title`/`metadata` + unique `source_id`) và 003
  (`collector_health`) — chạy `python -m storage.migrate up` sau khi pull.
- **`storage/pipeline_state.py`** (migration 008, issue #90) — kv scalar bền
  vững giữa các lần chạy (`get_state`/`set_state`/`get_int`/`set_int`). User đầu
  tiên: con trỏ offset của `import_daily` (HF). Generic — collector/job nào cần
  nhớ 1 scalar (cursor/last-id/timestamp) qua các lần chạy dùng chung, thay vì
  file state (không sống sót re-clone). `get_int` value hỏng → default (self-heal).

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
  `total` cao. Story đạt ngưỡng (`config.DRAMA_SCORE_THRESHOLD`, mặc định **4/6**)
  giữ nguyên `status='pending'`, sẵn sàng cho rewriter.
  **Nới ngưỡng + tinh chỉnh rubric (issue #86 follow-up):** ở 5/6, vì `safe=1`
  bắt buộc, story phải trúng 4/5 tiêu chí nội dung — nhưng drama Lemmy thật
  (relationship_advice/aita/asklemmy) hiếm khi có TWIST kiểu phim nên đa số kẹt
  ở 4/6 → cả batch ~4 story/ngày ra 0 pass → 0 video. Fix: (1) hạ mặc định
  xuống **4/6** (= safe + 3 tín hiệu nội dung, còn hạ được xuống 3 qua env ngày
  nguồn quá mỏng); (2) prompt rubric nới **TWIST** (chấp nhận leo thang/tiết lộ
  đẩy cảm xúc, không bắt buộc cú lật kiểu phim) và định nghĩa lại **SAFE** để
  chặn *mô tả trần trụi/đồ hoạ* chứ không chặn *chủ đề* nhạy cảm (ngoại tình/ly
  hôn/mâu thuẫn gia đình VẪN an toàn — đó là chất liệu drama). Review gate người
  thật (`review_bot`) vẫn đứng giữa đây và publish nên nới bộ lọc không đồng
  nghĩa đăng bừa.
- **`processors/drama_rewriter.py`** — module quan trọng nhất: Việt hoá story
  bằng Sonnet (đổi tên/địa điểm sang VN, thêm `vn_commentary` ≥20% thời
  lượng). `validate_rewrite()` là heuristic gate độc lập với prompt (word
  count, `vn_commentary` ≥`DRAMA_COMMENTARY_MIN_WORDS`, hook ngắn — proxy cho
  cấu trúc "Hook 3s", chặn tên/từ văn hoá Mỹ lọt qua).
  **Format short 2-3 phút (prompt v2, yêu cầu chủ kênh):** format v1 (script
  800-1200 từ, commentary ≥200) ra video ~6 phút — đo trên output thật, giọng
  TTS drama đọc ~210-230 từ/phút chứ KHÔNG phải "60-90 giây" như prompt v1
  ghi (cùng lớp lỗi word-count/duration của Phase 3/4). Mục tiêu 2-3 phút ⇒
  tổng narration ~420-650 từ: `prompts/drama/rewriter.v2.txt` nhắm script
  250-400 từ + commentary 80-120 từ + cấm script mở đầu bằng lặp lại
  hook/title; các dải validate hạ tương ứng (soft 250-400, hard 150-600,
  commentary min 60 — đều env-overridable). Rewriter chọn version qua
  `DRAMA_REWRITER_PROMPT_VERSION` (mặc định **v2**, per-prompt — KHÔNG đụng
  `PROMPT_VERSION` global vì scorer/theme_detect/longform vẫn v1); rollback
  format 6 phút = đặt lại `v1` + nới các dải qua env. Rewrite hợp lệ →
  `status='approved'`; không hợp lệ → `status='needs_review'` + alert
  Telegram (nhưng output vẫn được lưu để người xem lại, không bị huỷ).
  **Beat "cộng đồng phán xử" — `vn_reactions` (issue #92 follow-up):** prompt v1
  giờ có field `vn_reactions` — khi STORY GỐC mang mục "TOP COMMENTS FROM REDDIT"
  (do `hf_drama_importer` gắn vào `raw_content`), model VIỆT HOÁ tinh thần các
  comment đó thành 2-4 câu bình luận giọng cư dân mạng VN (văn xuôi, không giữ
  YTA/NTA), đặt gần cuối để **tăng hook + mời người xem vào bình luận**. Field
  **OPTIONAL** (story không có comment → rỗng), nên KHÔNG nằm trong
  `_REQUIRED_FIELDS`, không block validate; nhưng khi có thì vẫn bị scan luật
  Việt-hoá (tên/từ Mỹ). `main_drama.build_narration` chèn `vn_reactions` SAU
  script, TRƯỚC `vn_commentary` (story → đám đông phán xử → góc nhìn người kể +
  CTA); story cũ không có field này → narration không đổi.
  **Word count 2 dải (issue #86):** prompt vẫn nhắm 800-1200 từ, nhưng LLM
  không bao giờ trúng chính xác — story #2 ra 733 từ (script hoàn chỉnh, chỉ
  thiếu 67 từ) bị reject y như stub gãy → cả run render **0 video**. Fix: tách
  "đích lý tưởng" khỏi "sàn reject". `_script_length_verdict()` phân loại:
  `[HARD_MIN, HARD_MAX]` (mặc định 600-1500) = **chấp nhận**; ngoài dải lý
  tưởng `[SOFT_MIN, SOFT_MAX]` (800-1200) nhưng còn trong dải chấp nhận →
  approve + log note (quan sát model có hay ngắn/dài không); chỉ dưới `HARD_MIN`
  (stub/cắt cụt) hoặc trên `HARD_MAX` (runaway/lặp) mới block. Cả 4 ngưỡng
  env-overridable (`DRAMA_SCRIPT_{SOFT,HARD}_{MIN,MAX}_WORDS`).
  **Hook 2 dải (issue #99):** hook là length-check duy nhất còn sót hard
  threshold sau #86 — story 574 có hook **26 từ** (vượt đúng 1 từ so với trần
  cứng 25) bị block y như script gãy → `needs_review`, mà `needs_review` với
  STORY là ngõ cụt (review_bot chỉ review video; `rewrite_all_scored` chủ động
  skip). Fix cùng thiết kế 2 dải: `validate_rewrite_verdict()` trả
  `(blocking, soft_notes)` — hook ≤`DRAMA_HOOK_SOFT_MAX_WORDS` (25) = lý tưởng;
  vượt tới `DRAMA_HOOK_HARD_MAX_WORDS` (35) = **approve + log note** (không
  alert Telegram — alert là cho story cần người, note thì không); chỉ >35 từ
  (đoạn văn, không phải hook) mới block. `validate_rewrite()` giữ nguyên API
  (chỉ trả blocking). **Gỡ kẹt story cũ:** `python -m processors.drama_rewriter
  --revalidate` re-chạy validation trên story drama `needs_review` bằng
  ngưỡng HIỆN TẠI từ `rewritten_content` đã lưu (zero call AI, zero cost),
  approve story giờ pass; envelope `_rewrite_error` (reply không parse được)
  bị bỏ qua. Chạy 1 lần sau khi deploy để cứu story 574.
  **Cải tiến so với tài liệu gốc:** 2 rule phòng rủi ro mà tài liệu liệt kê
  ở mục "Rủi ro" (tên thuần Việt 2-3 từ, không nhắc văn hoá Mỹ) được đưa
  thẳng vào prompt v1 luôn, không đợi tune sang v2 — xem
  `docs/current/prompts-decisions.md`.
  **Xử lý lỗi output JSON (issue #82):** root cause = `max_tokens` cũ (2000)
  quá thấp cho output bắt buộc (script 800-1200 từ + `vn_commentary` ≥200 từ +
  JSON wrapper). Tiếng Việt token hoá ~2 token/từ (dấu) nên ngay cả output
  ngắn nhất cũng ~2500+ token → Sonnet bị cắt GIỮA JSON (`stop_reason='max_
  tokens'`), thiếu dấu `}` đóng → regex `\{.*\}` không match → lỗi lệch hướng
  "No JSON object found"; 3 retry cùng tham số cắt y hệt → 0 video. Fix:
  (1) `config.DRAMA_REWRITER_MAX_TOKENS` mặc định **4096** (khớp
  `drama_compiler`), env-overridable; (2) phân biệt cắt-cụt (`stop_reason=
  max_tokens`) với từ-chối/prose (`end_turn`) — cắt-cụt thì **tăng `max_tokens`
  ×1.5 mỗi retry** (4096→6144→9216) để story dài bất thường vẫn xong thay vì
  cắt lặp; (3) LOG cả `stop_reason` + đoạn đầu reply thật (trước đây vứt đi
  reply nên không phân biệt được 3 giả thuyết); (4) hết retry mà model CÓ trả
  lời nhưng không parse được (từ chối/cắt-cụt dai dẳng) → `status='needs_
  review'` + lưu raw reply vào `rewritten_content` (envelope `{_rewrite_error,
  _stop_reason, _raw_reply}`) + alert, thay vì để `pending` nã token Sonnet mỗi
  ngày; chỉ lỗi API thật (chưa chạm được model) mới giữ `pending` để retry.
  `_alert_validation_failure` best-effort (nuốt lỗi notifier, không phá cả
  batch).
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

- **`video/tts/`** (per-track voice + speed) — thay vì xây lại abstraction TTS
  mới như tài liệu đề xuất (EPIC #4.1: `ElevenLabsProvider`/`FPTAIProvider`),
  package `video/tts/` (base/factory/nuitruc/edge) đã có sẵn và đủ tốt — chỉ
  thêm `voice_id` **và `speed`** xuyên suốt chain (`TTSProvider.synthesize`,
  `factory.synthesize`, `tts_client.text_to_speech`, nuitruc `_submit_job`,
  edge). `voice_id` là opaque per-provider (bị bỏ khi fallback sang provider
  khác); `speed` là hệ số chung nên GIỮ qua fallback. Hàm
  `tts_client.synthesize_for_track(text, track, output_path)` tra
  `config.tts_profile_for_track(track)` — single source of truth:
  **ai → (`voice1`, 1.5), drama → (`preset_my_duyen`, 1.0)**, tất cả
  env-overridable (`TTS_VOICE_ID_AI`/`TTS_VOICE_SPEED_AI`/`TTS_VOICE_ID_DRAMA`/
  `TTS_VOICE_SPEED_DRAMA`). Voice id rỗng → voice mặc định của provider. Provider
  mặc định là nuitruc API (`TTS_PROVIDER=nuitruc`). Track AI (`main.py`) cũng
  dùng `synthesize_for_track("ai", ...)` nên 2 kênh có giọng/tốc độ riêng.
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
  video rỗng).
  **Nền scene "đơn sắc" (issue #103):** template gốc CHỦ ĐÍCH chỉ cho 3/6
  scene dùng illustration (còn lại gradient/solid), và mỗi lần Replicate lỗi
  scene rơi thẳng về `solid_blue` — video thật ra 1 ảnh AI + 5 mảng màu (ảnh
  scene 0 thường chỉ là CACHE HIT từ thumbnail cùng `(prompt, index=0)`, các
  scene index khác gọi API thật và chết chùm). Fix 4 lớp: (1) template
  illustration-first cho MỌI scene, màu cũ chuyển vào field `fallback`
  declarative (chỉ dùng khi hết đường ảnh); scene i dùng variant
  `i % DRAMA_ILLUSTRATION_VARIANTS` (mặc định 3 — đa dạng mà không nhân call
  theo số scene, variant 0 trùng cache thumbnail nên 1 call luôn tái dùng);
  (2) thứ tự resolve: `generate_illustration` (cache-hit free) → **memo lỗi
  trong run** (fail live 1 lần = các scene sau bỏ qua API, khỏi đốt poll-timeout
  90s×5) → `image_generator.cached_illustration()` tái dùng BẤT KỲ variant đã
  cache của prompt (zero cost) → **Pexels PHOTO fallback** (yêu cầu chủ kênh
  07/2026: mọi scene drama phải là ẢNH — `pexels_downloader.get_photos()` search
  theo chính `thumbnail_prompt`, không ra thì query mood chung
  `DRAMA_PHOTO_GENERIC_QUERY`; cache-first theo (query, orientation, index),
  memo kết quả trong run — 1 lookup/video; tắt qua
  `DRAMA_PHOTO_FALLBACK_ENABLED=0`) → `fallback` của scene → `solid_blue` chót
  (chỉ còn thấy khi CẢ Replicate lẫn Pexels đều bất khả dụng);
  (3) scene nền ảnh có **Ken Burns** (zoompan, hướng zoom xen kẽ theo scene,
  `DRAMA_SCENE_MOTION=1`; render motion lỗi tự retry static — nice-to-have
  không được phá video); `illustration_dark` giờ thật sự TỐI (eq dim +
  desaturate, áp TRƯỚC overlay nên commentary card giữ nguyên độ sáng);
  (4) mọi segment chuẩn hoá `fps=30` (lavfi mặc định 25 ≠ zoompan 30 sẽ làm
  concat `-c copy` lệch timestamp). Nhạc nền: nếu `ENABLE_BGM=1`, mix nhạc từ
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

## Distribution Layer (Phase 5)

Xem `docs/current/phase-5-detailed.md`/`phase-5-issues.md`. Đưa video đã
render tới đúng kênh: **tự phát hành** (`review_bot.auto_dispatch`) → YouTube xếp
lịch theo cadence + tự upload, TikTok gửi Telegram đăng tay. Track Drama chạy
end-to-end qua `main_drama.py`. (Review gate ✅/❌/✏️ vẫn còn cho duyệt tay video
cũ/`needs_review` — không còn chặn video mới, xem `review_bot.py` bên dưới.)

- **`notifier/review_bot.py`** — routing + review handlers.
  **Khác với tài liệu thiết kế:** doc vẽ `review_bot.py` như bot mới; thực tế
  đây là các handler THUẦN được `telegram_bot._handle_update()` /
  `_handle_callback_query()` gọi trong CÙNG vòng long-polling — cùng lý do
  seed_bot Phase 2 (2 process cùng token → 409 Conflict).
  **TỰ PHÁT HÀNH — bỏ nút ✅ chặn cho YouTube (yêu cầu chủ kênh):** ban đầu
  Phase 5 bắt CẢ 2 track AI + Drama qua review gate (bấm ✅ mới xếp lịch). Nhưng
  chủ kênh đã có CADENCE tự đăng YouTube nên KHÔNG muốn duyệt tay YouTube — chỉ
  muốn xem trước video TikTok để đăng tay. Nay `auto_dispatch(video_id)` thay
  `push_review` ở bước render (main.py/main_drama.py): claim `ready`→`approved`
  (idempotent, chống dispatch trùng khi resume) rồi route MỌI kênh qua
  `_route_all` (dùng chung với `_approve`): **YouTube → `schedule_video` (CADENCE,
  tick tự upload); TikTok → `send_tiktok_manual` (Telegram Bé MC đăng tay)** +
  1 tin Telegram **FYI không nút chặn** (đính kèm preview NẾU video chưa tới
  Telegram qua đường TikTok — tránh gửi trùng file; video long chỉ-YouTube thì
  luôn kèm preview). `_send_dispatch_fyi` best-effort (nuốt lỗi notifier). Video
  kẹt `ready` (dispatch lỗi) được `_dispatch_stuck_videos`/`_dispatch_ready_ai_videos`
  thử lại lần chạy sau. **Review gate ✅/❌/✏️ (`_approve`/`_reject`/`handle_callback`)
  GIỮ NGUYÊN** cho video cũ đang `pending_approval` + duyệt tay khi cần (vd
  `needs_review`), chỉ không còn là đường mặc định của video mới.
  Kênh TikTok KHÔNG auto-schedule — video được gửi qua Telegram tới
  kênh "Bé MC" (`telegram_bot.send_tiktok_manual`) để upload tay
  (`_route_to_channel`). ❌ Reject → hỏi lý do, lưu `videos.review_note`;
  ✏️ Edit → FSM chọn field (allowlist trong
  `storage.database._VIDEO_METADATA_FIELDS`) → nhập giá trị. State chờ input
  lưu `notifier/.review_state.json` (persisted qua restart); `/skip` huỷ.
  Preview >50MB được NÉN (2 nấc 720p/CRF28 → 480p/CRF32,
  `video/preview.py`) thay vì bỏ qua như trước — file gốc không bị đụng;
  nén thất bại → fallback script-only review như cũ (issue #60). Flow duyệt
  cũ (`send_video_for_approval`, track AI) cũng dùng compressor này.
  **Nút "Duyệt" không phản hồi (issue #88) — 3 root cause đã sửa trong
  `telegram_bot.py`:** (1) *409 Conflict vĩnh viễn* — webhook tồn đọng loại
  trừ long-poll nên MỌI `getUpdates` trả 409 bất kể số instance;
  `run_bot` gọi `_delete_webhook()` (giữ pending) lúc khởi động, và `_get_updates`
  tự chữa + lùi 5s khi gặp 409 thay vì nuốt lỗi rồi busy-loop nã API/spam log.
  (2) *answerCallbackQuery 400 "query too old"* — `_handle_callback_query` **ack
  callback TRƯỚC** khi chạy `handle_callback` (approve = ghi DB + xếp lịch + gửi
  file mất vài giây, quá cửa sổ vài giây của Telegram) → nút nhả tức thì (toast
  "⏳ Đang xử lý…"), việc nặng chạy sau, kết quả gửi bằng message riêng; 400 kiểu
  này là LÀNH nên log `info` kèm `callback_id` + `description` thật (đọc body
  HTTPError qua `_read_error_body`, `str()` giấu mất) thay vì ERROR. (3) *plist
  deploy tay còn `/Users/YOU`* → launchd exec fail EX_CONFIG, bot không chạy:
  `launchd/install.sh` render placeholder sẵn, nay thêm guard từ chối cài bản
  còn sót placeholder / cảnh báo wrapper không tồn tại. Watchdog service kẹt
  (issue #74/#75) đã có sẵn ở `storage/launchd_status.py`.
- **`publisher/youtube_uploader.py`** — `upload_to_youtube(video_id,
  channel_key)`: token OAuth tra qua `channels.py[key]["oauth_token_env"]` →
  env var trỏ tới file token (đúng convention Phase 1/oauth-setup.md,
  **không** hardcode `tokens/{key}.json` như doc). Resumable upload retry
  transient (429/5xx/mạng) backoff 2/4/8/16s; thumbnail
  (`videos.thumbnail_path`) + caption track upload best-effort SAU khi có
  `youtube_video_id` — fail sau điểm đó chỉ warning, không kích re-upload.
  **Khác doc:** categoryId track AI giữ 28 (Science & Tech, hành vi cũ) thay
  vì 22; Drama dùng 24 (Entertainment) như doc. `YOUTUBE_PRIVACY=unlisted`
  để chạy E2E test không public.
- **`scheduler/post_scheduler.py`** — `CADENCE` key theo `(channel_key,
  track, video_type)`. **Lịch phát sóng thống nhất cả 2 kênh YouTube:** short
  đăng **Thứ 2–7** (slot spec `"mon-sat 12:00"`), long đăng **Chủ nhật**
  (`"sun 20:00"`) — khớp lịch sản xuất `publisher/scheduler.py`. Slot spec hỗ
  trợ weekday-range/list (`mon-sat`, `mon,wed,fri`) qua `_parse_weekday_token`.
  CADENCE **không còn entry TikTok** (TikTok chuyển sang gửi Telegram tay).
  `schedule_video()` idempotent (video đã có post queued/uploading/done cho
  kênh đó → trả post cũ); slot trống dò tuần tự, double-book bị chặn thêm bằng
  partial unique index. `tick` (launchd `com.ai5phut.post-scheduler.plist`, 5
  phút/lần) claim atomic queued→uploading rồi upload; **post kẹt 'uploading'
  không bao giờ tự retry** — video có thể ĐÃ lên platform trước khi crash, chỉ
  alert Telegram để xử lý tay (chống upload trùng, rủi ro §5 của doc). Nhánh
  `_dispatch` cho TikTok (nếu còn post cũ) gửi Bé MC thay vì auto-upload.
- **TikTok = gửi Telegram (kênh "Bé MC") + upload tay:** thay cho auto-upload
  API. `telegram_bot.send_tiktok_manual(video_id)` gửi **NARRATIVE
  (`script_text` — chính narration đọc trong video) TRƯỚC, rồi FILE GỐC** (giữ
  chất lượng) tới `config.TELEGRAM_TIKTOK_CHAT_ID` (rỗng → `TELEGRAM_CHAT_ID`)
  — yêu cầu chủ kênh 07/2026: Bé MC nhận cả text lẫn video cho MỌI video
  TikTok, cả track AI lẫn Drama (cả 2 đều route qua hàm này). Text lỗi không
  chặn gửi video (best-effort); text dài auto-split qua `_send_text_chunks`
  (giờ nhận `chat_id`). File >50MB (trần Telegram bot) → fallback export ra
  `queue_tiktok/` + nhắn đường dẫn. `publisher/tiktok_manual.
  export_for_manual_upload` giờ chỉ là fallback lưu file gốc, không còn là
  đường chính. **Báo cáo "TÓM TẮT AI HÔM NAY (5 bài)" TẮT mặc định**
  (`AI_NARRATIVE_REPORT_ENABLED=0`, cùng lý do — narrative đã tới Bé MC theo
  từng video): `main.py` vẫn SINH narrative (video cần nó), chỉ bước gửi
  Telegram bị tắt; bật lại qua env.
- **`main_drama.py`** — orchestrator: collect → score → rewrite → render
  (TTS voice drama + `compose_drama_video`) → `auto_dispatch` (tự phát hành:
  YouTube cadence + TikTok Telegram tay, không nút ✅). **Bước collect gọi nhiều
  nguồn độc lập, best-effort (một nguồn lỗi không kéo cả bước): Reddit (tắt mặc
  định), Lemmy (`collect_all_lemmy` — topping tươi best-effort), và HF hàng ngày
  (`import_daily` chế độ `cursor` mặc định, hoặc `import_dataset(newest=True)` khi
  `HF_DRAMA_DAILY_MODE=newest`; gác bởi `HF_DRAMA_DAILY_ENABLED`, mặc định **ON**
  từ issue #90 — HF là nguồn drama TIN CẬY, xem Phase 2). Seed thủ công
  (`seed_bot`) vẫn chảy qua vì score/rewrite/render đọc thẳng bảng `stories`.**
  Chọn story theo `created_at DESC` nên nội dung mới nạp (HF/Lemmy hôm nay) luôn
  được ưu tiên trước backlog cũ.
  **Fix "đọc tiêu đề 2 lần + phụ đề lệch audio":** model hay lặp lại hook/title
  ở câu mở đầu script với khác biệt dấu câu/vài chữ; check containment RAW cũ
  (`hook not in script`) trượt → narration đọc trùng, TỆ HƠN:
  `subtitle_generator._split_into_segments` lại XOÁ segment trùng khỏi phụ đề
  (audio vẫn đọc) nên mọi phụ đề sau điểm đó lệch timing (chế độ wordcount chia
  thời lượng theo tỉ lệ từ). Fix 2 đầu: (1) `build_narration` dedupe theo NỘI
  DUNG ĐỌC — `_spoken_duplicate()` chuẩn hoá (bỏ dấu câu/hoa thường) rồi check
  containment + fuzzy match `SequenceMatcher ≥0.8` với ĐOẠN MỞ ĐẦU script (chỉ
  prefix, không trượt cửa sổ cả bài — câu tình cờ giống ở giữa chuyện không cắt
  oan hook); (2) `subtitle_generator` KHÔNG xoá segment trùng nữa — phụ đề phải
  phản chiếu đúng những gì audio đọc, dedupe thuộc về tầng dựng narration (một
  nguồn text cho cả TTS lẫn SRT). Prompt rewriter v2 cũng cấm script mở đầu
  bằng hook (phòng từ gốc; check code là enforcement cho story cũ). Resume: trạng
  thái nằm hết trong DB; video row được insert TRƯỚC khi render (gắn
  `videos.story_id`) — lỗi transient PHÁT HIỆN ĐƯỢC (TTS/ffmpeg trả lỗi) →
  row `failed`, lần chạy sau tự retry; crash thật (row kẹt `draft`) chặn
  auto-render lại, chờ xử lý tay — không bao giờ render trùng. Narration =
  hook + script + `vn_reactions` (beat cộng đồng phán xử, optional) +
  vn_commentary (check containment tránh đọc lặp — prompt rewriter không nói rõ
  script có chứa hook hay không). Chạy 06:40
  (`com.ai5phut.drama-pipeline.plist`), sau collector 06:06, trước pipeline
  AI 07:00.
- **`storage/quota.py`** — đếm unit YouTube API (upload 1600, thumbnail 50,
  caption 400) theo NGÀY PACIFIC (quota Google reset nửa đêm PT, không phải
  giờ VN); alert Telegram đúng 1 lần khi băng qua
  `YOUTUBE_DAILY_QUOTA × QUOTA_ALERT_RATIO` (mặc định 10000 × 0.8).
- **`webui/health.py`** — `python -m webui.health` → `GET /health`
  (127.0.0.1, port `HEALTH_PORT`=8686): videos/stories theo status, queue
  scheduler, quota hôm nay, last_success collectors. Mỗi section bọc lỗi
  riêng — DB thiếu bảng không làm sập cả payload.
- Migration 006 (`scheduled_posts`, `quota_usage`, cột
  `videos.story_id/thumbnail_path/review_note`) — chạy
  `python -m storage.migrate up` sau khi pull.
- TikTok Content Posting API uploader (`publisher/tiktok_uploader.py`) có từ
  trước, giữ nguyên — phần "3-step upload" của doc đã được cover; approval
  app TikTok là task external (2-4 tuần), pipeline không block nhờ queue tay.
- **`publisher/token_health.py` — giám sát OAuth token YouTube (issue #94).**
  Root cause #94: token kênh `drama_youtube` hết hạn + refresh_token bị Google
  thu hồi (`invalid_grant`) từ 10/07 mà **6 ngày không ai biết** → video 117 kẹt
  không upload. Cron check cũ (1) chỉ soi MỘT file token cứng
  (`publisher/.youtube_token.json` = token mặc định ai_youtube) nên **mù**
  drama_youtube; (2) tự nó timeout 30s → fail 13 lần liên tiếp vì
  `creds.refresh(Request())` của google-auth **không đặt socket timeout** (treo
  vô hạn khi endpoint chậm). Fix đúng gốc: `check_and_alert()` lặp qua **MỌI
  kênh** `channels.channels_for_platform("youtube")` (single source of truth,
  không hardcode file) và **probe** refresh_token bằng stdlib `urllib` với socket
  timeout `TOKEN_HEALTH_TIMEOUT` (mặc định 15s, **fail-fast** thay vì treo — sửa
  root cause #3); HTTPS verify mặc định, KHÔNG log token/secret. Chỉ probe (mint
  thử access token, KHÔNG ghi đè file) → không rotate/không đua ghi. Phân loại:
  `ok` / `revoked` (invalid_grant → alert kèm lệnh cấp lại `--token-file`) /
  `missing` / `no_refresh_token` / `unreadable` / `misconfig` (alert ngay, nhắc
  hằng ngày tới khi cấp lại) / `transient` (timeout/mạng/5xx/429 — KHÔNG spam,
  chỉ alert khi lặp `TOKEN_HEALTH_TRANSIENT_ALERT_AFTER`=3 lần liên tiếp để
  chính "monitor không tới được Google" cũng lộ ra — đếm bền vững qua
  `pipeline_state`, DB chưa migrate 008 → degrade êm). Best-effort, không raise
  (như `collector_health`). Chạy độc lập `python -m publisher.token_health`
  (08:00, `launchd/com.ai5phut.token-health.plist`) VÀ ké best-effort trong
  `main.run_pipeline` (defense-in-depth như `launchd_status`). **Khi token bị
  thu hồi phải cấp lại thủ công:** `cd content-pipeline && python
  publisher/youtube_uploader.py --token-file <file> --force-reauth` (xem
  `docs/current/oauth-setup.md` §1.5) — `--force-reauth` bỏ token cũ đã thu hồi
  rồi mint mới (rerun thường sẽ `refresh()` token chết → `invalid_grant` trước
  khi mở browser). Code không tự re-auth được. Monitor cũng bắt kênh **dùng
  chung file token** (`unconfigured`, chưa có token riêng) và token **thiếu
  scope** `youtube.force-ssl` (refresh OK nhưng uploader loại bỏ → kích OAuth
  giữa lúc upload) — cả hai đều alert trước giờ upload (review PR #95).
- **`video/asset_key_health.py` — giám sát API key asset (follow-up #94).**
  Cùng tinh thần `token_health` nhưng cho **API key TĨNH** của nhà cung cấp asset
  (không phải OAuth refresh): **Pexels** (`PEXELS_API_KEY`, nền b-roll cả 2 track)
  và **Replicate** (`REPLICATE_API_TOKEN`, minh hoạ AI track Drama). Gọi 1 request
  xác thực nhẹ bằng stdlib `urllib` + socket timeout (`ASSET_KEY_HEALTH_TIMEOUT`
  =15s): 200 = `ok`, 401/403 = `invalid` (alert), 5xx/429/timeout = `transient`
  (đếm bền vững `pipeline_state`, chỉ alert khi lặp `ASSET_KEY_HEALTH_TRANSIENT_
  ALERT_AFTER`=3). **Bất đối xứng theo thiết kế:** Pexels rỗng = `missing` →
  **alert** (nền hỏng thì mọi video mới dùng lại nền cache CŨ — hỏng IM LẶNG,
  không crash); Replicate rỗng = `disabled` → **KHÔNG alert** (tuỳ chọn, composer
  fallback gradient). KHÔNG log giá trị key. Chạy độc lập `python -m
  video.asset_key_health` (08:10, `launchd/com.ai5phut.asset-key-health.plist`)
  VÀ ké best-effort trong `main.run_pipeline`. **Cấp/cập nhật key:** Pexels miễn
  phí tại https://www.pexels.com/api/, Replicate tại
  https://replicate.com/account/api-tokens — đặt vào `.env`.
- **User-Agent bắt buộc cho mọi request urllib tới API sau CDN/WAF (issue #97).**
  Root cause #97: urllib mặc định gửi UA `Python-urllib/3.x` → **Cloudflare chặn
  với 403 body `error code: 1010`** dù key hoàn toàn đúng — Pexels/Replicate "chết"
  từ 01/07 mà log lại đổ oan cho key. Fix: `config.HTTP_USER_AGENT` (env-overridable,
  mặc định `Mozilla/5.0 (compatible; ContentPipelineBot/1.0)`) được gắn vào MỌI
  request của `pexels_downloader` / `image_generator` / `asset_key_health`. Đồng
  thời **phân biệt WAF-block với key sai** bằng cách đọc body lỗi (`error code:
  10xx`): `asset_key_health` trả trạng thái `blocked` riêng (alert nói rõ "key
  KHÔNG sai, đừng thay key — kiểm tra HTTP_USER_AGENT/IP"), còn
  `pexels_downloader` set sentinel `_last_pexels_error='blocked'` (vẫn dừng vòng
  query như `'auth'` — nã tiếp WAF chỉ phí request — nhưng log đúng nguyên nhân).
  Module mới gọi API ngoài qua urllib: LUÔN gửi `config.HTTP_USER_AGENT` (trừ
  Reddit/Lemmy đã có UA riêng theo quy định API của họ).

---

## Analytics Layer (Phase 6)

Xem `docs/current/phase-6-detailed.md`/`phase-6-issues.md`. Đo lường để học:
pull metric YouTube/TikTok → snapshot theo ngày → dashboard KPI + A/B compare +
báo cáo tuần Telegram. Ngoài phạm vi: predictive model, auto-tune prompt.

- **`storage/video_metrics.py` + `channel_metrics.py`** — snapshot số liệu
  video / kênh. **Khác `phase-6-detailed.md`:** `video_metrics.video_id` để
  NULLABLE (doc ghi NOT NULL) và khoá upsert là `(platform, external_id,
  snapshot_date)` thay vì `(video_id, snapshot_at)` — vì metric TikTok giai
  đoạn manual (CSV) không có đường map đáng tin về `videos.id`, ép NOT NULL =
  vứt hết số liệu TikTok. `video_id` là FK best-effort, điền khi map được qua
  `scheduled_posts.platform_video_id`. Upsert idempotent trong ngày (pull lại
  chỉ ghi đè — metric YouTube trễ 24-48h).
- **`storage/cost_logs.py` + `analytics/pricing.py`** — hiện thực hoá bảng
  `cost_logs` mà `phase-6-detailed.md` GIẢ ĐỊNH "đã ghi từ Phase 3/4" (thực tế
  `ai_usage.py` xưa chỉ log ra Python logging, KHÔNG persist). Nay
  `ai_usage.log_token_usage()` persist token thô vào `cost_logs` (best-effort,
  non-fatal), và `ai_scorer`/`ai_analyzer` cũng gọi vào đó. Lưu **token thô**
  (không phải $); quy đổi USD là overlay ở `pricing.py`, đổi giá qua env
  `PRICE_<MODEL>_IN/_OUT` không cần migrate dữ liệu — đúng tinh thần `ai_usage`
  cố ý không nhét pricing vào hot path (giá stale âm thầm).
- **`analytics/youtube_puller.py`** — pull qua YouTube Analytics API v2
  (`ids=channel==MINE`), cả per-video lẫn per-channel (sub growth). `retention
  _50_pct` lấy từ report `audienceRetention` tại mốc 0.5 (best-effort, video
  thiếu dữ liệu → None). Scope OAuth chỉ-đọc KHÁC token upload → dùng token
  riêng `<upload_token>.analytics.json`; cấp: `python -m analytics.youtube_
  puller auth <channel_key>`. Cron 23h (`com.ai5phut.metrics-pull.plist`).
- **`analytics/tiktok_csv.py` + `notifier/analytics_bot.py`** — giai đoạn 1
  (chưa có TikTok API): `/import_tiktok_csv` → đính kèm CSV từ TikTok Studio →
  `telegram_bot._download_file()` tải file → parse (khoan dung header Anh/Việt,
  số "1.2K"/"45%"/"0:12") → upsert. Handler THUẦN gọi trong cùng vòng
  long-polling (như seed_bot/review_bot — 1 token/1 poll).
- **`analytics/experiment_compare.py` + `stats.py`** — tag video qua
  `database.set_video_experiment(id, experiment_id, arm)` (cột mới
  `videos.experiment_id/experiment_arm`), so 2 arm theo metric thật bằng
  Welch's t-test (p-value qua incomplete-beta, KHÔNG cần scipy). Chặn kết luận
  non non mẫu: `enough_samples` (≥5/arm) vs `recommended_samples_met` (≥10/arm,
  quy tắc cứng §5). Khác `ab_harness.py` (A/B prompt tầng story) — cái này tầng
  video sau khi đăng. 3 thí nghiệm đầu: `docs/current/experiments-log.md`.
- **`analytics/weekly_retro.py`** — báo cáo tuần ≤1500 ký tự (vừa 1 message
  Telegram): top 3 view, bottom 3 retention, sub growth từng kênh, chi phí AI,
  action items (chỉ nêu tín hiệu, KHÔNG tự cắt format — để `experiment_compare`
  quyết). Cron CN 19h (`com.ai5phut.weekly-retro.plist`). `generate_retro_
  report()` pure (test được), `send_weekly_retro()` mới gọi Telegram.
- **`dashboard/data.py` (thuần) + `dashboard/app.py` (Streamlit)** — 4 tab
  Overview/Top videos/Format/Cost. Toàn bộ logic dữ liệu ở `data.py` (test
  không cần streamlit); `app.py` lazy-import streamlit, thiếu thì báo cài.
  `webui/health.py` thêm section `analytics` (snapshot 7 ngày + cost 7 ngày).
- Migration 007 (`video_metrics`, `channel_metrics`, `cost_logs`, cột
  `videos.experiment_id/experiment_arm`) — chạy `python -m storage.migrate up`
  sau khi pull. **Khác `phase-6-issues.md`** (đặt tên `002_metrics_schema.sql`):
  tiếp dãy số liên tục tới 007, không reset về 002 (đụng 002 đã tồn tại).

---

## Vận hành launchd (Mac Mini) — issue #72/#74/#75

Xem `launchd/README.md`. Mọi service chạy nền là launchd LaunchAgent
(`launchd/com.ai5phut.*.plist`), cài/refresh idempotent qua `launchd/install.sh`
(`install`/`status`/`reload [label]`/`uninstall`).

- **Root cause (đã kiểm chứng, không còn phỏng đoán):** launchd/`xpcproxy` dựng
  `WorkingDirectory` + `StandardOutPath`/`StandardErrorPath` **trước khi** exec
  binary và giữ **handle inode** của các thư mục đó. Khi thư mục bị **xoá & tạo
  lại** (rebuild venv, re-clone repo, reconfig `.env`/token) handle thành stale →
  xpcproxy setup fail, trả `EX_CONFIG` (exit **78**) *trước khi binary chạy* (nên
  log file không hề được tạo, còn foreground vẫn chạy tốt). Exit 78 **khoá** job
  vào "spawn scheduled" — KeepAlive cũng không restart — tới khi `reload`. Đây là
  lý do `pipeline` (đã dùng wrapper từ trước) VẪN chết còn `bot` (KeepAlive, tiến
  trình thường trú không re-spawn) sống sót.
- **Fix tận gốc — plist KHÔNG khai báo `WorkingDirectory`/`StandardOutPath`/
  `StandardErrorPath`.** Plist chỉ còn trỏ vào **một path string**: wrapper tĩnh
  (`run_pipeline.sh` cho pipeline/bot, `run_module.sh` cho phần còn lại; launchd
  re-resolve path mỗi spawn, không giữ handle thư mục). Wrapper thiết lập **lại**
  cwd (`cd`) + log (redirect vào `logs/${LOG_BASENAME}_stdout/stderr.log`) ở
  **runtime với inode tươi** → rebuild venv/re-clone không còn phá scheduled run.
  `run_pipeline.sh` uỷ cho `run_module.sh` (1 chỗ resolve venv + cwd + log). Thêm
  plist mới → trỏ `run_module.sh` + đặt `LOG_BASENAME`, giữ quy ước **tên file
  plist == Label**.
- **`storage/launchd_status.py` — watchdog + self-heal (defense-in-depth).** Chạy
  ké trong `main.py` (07:00) và `storage.collector_health` (06:30/18:30),
  best-effort không bao giờ raise. Ngoài việc alert service **chưa load** (#72),
  nay đọc cột Status của `launchctl list` (không tốn call thêm) để bắt service
  **đã load nhưng fail**; service kẹt `EX_CONFIG` (78) được **tự re-bootstrap** qua
  `install.sh reload <label>` rồi alert — vì reload là cách DUY NHẤT gỡ job kẹt 78.
  Truyền `self_label` để watchdog KHÔNG tự bootout chính service đang chạy nó. Trên
  máy không phải macOS mọi hàm trả `None`/`False` và không làm gì (non-fatal).

---

## Nguồn dữ liệu cần thu thập

### RSS Feeds (dùng feedparser)
- `https://www.therundown.ai/feed` — Newsletter AI hàng ngày
- `https://bensbites.beehiiv.com/feed` — Ben's Bites newsletter
- `https://vnexpress.net/rss/khoa-hoc-cong-nghe.rss` — VnExpress Công nghệ

> Reddit r/ChatGPT + r/artificial **không** lấy qua RSS nữa (issue #78): các
> endpoint `www.reddit.com/*.rss` bị chặn 403/429 cho client không xác thực và
> trùng lặp với `collectors/reddit_collector.py`. Track AI lấy 2 subreddit này
> qua JSON API đã xác thực (`reddit_client`) — xem Drama Source Layer (Phase 2).

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
| `VIDEO_CRF_LONG` / `VIDEO_CRF_SHORT` | `23` / `26` | CRF final encode theo loại video (issue #103). Short dùng CRF cao hơn — xem trên điện thoại + platform re-encode lại |
| `VIDEO_MAXRATE_KBPS_LONG` / `VIDEO_MAXRATE_KBPS_SHORT` | `4000` / `3000` | Trần bitrate final encode (kbps, bufsize = 2×); `0` = không cap (issue #103) |
| `DRAMA_ILLUSTRATION_VARIANTS` | `3` | Số ảnh minh hoạ AI riêng biệt cho 1 story drama; scene i dùng variant `i % N` (issue #103) |
| `DRAMA_SCENE_MOTION` | `1` | Zoom chậm (Ken Burns) trên scene drama nền ảnh tĩnh; `0` = ảnh đứng yên như cũ (issue #103) |

**Kích thước file output (issue #103):** cả 2 composer trước đây encode y hệt
nhau (`libx264 -crf 23`, KHÔNG set `-b:v` ở đâu cả — mô tả trong issue sai chỗ
này), nhưng CRF là "chất lượng cố định, bitrate thả nổi" nên AI short nền
b-roll chuyển động mạnh phình ~7.8Mbps/50MB trong khi drama nền tĩnh chỉ
~355kbps — cùng setting, khác nội dung. Fix: giữ CRF (quality-driven) nhưng
THÊM trần `-maxrate`/`-bufsize` per video type + CRF 26 cho short, qua
`config.encode_settings(video_type)` (single source of truth, cả engine ffmpeg
lẫn moviepy dùng chung). Chỉ áp cho FINAL encode — intermediate (scene segment,
multi-bg pre-pass) giữ CRF 23 vì sẽ được encode lại. Đo thật: nền chuyển động
mạnh 10s từ 23.4Mbps/29MB → 3.2Mbps/4.2MB (~7×), nền tĩnh không đổi (cap chỉ
chặn đỉnh). `-b:v 500k` như issue gợi ý bị TỪ CHỐI — bitrate ép cứng 500k làm
b-roll motion vỡ hình rõ ở 1080x1920.

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

**CTA đăng ký kênh (yêu cầu chủ kênh 07/2026):** mọi narration đăng YouTube
phải KẾT bằng câu kêu gọi đăng ký kênh. Script long track AI đã có sẵn trong
prompt; short AI trước chỉ có "Follow..." (giọng TikTok) và drama không có chỉ
dẫn CTA. Fix 2 tầng: (1) prompt — `SHORT_SCRIPT_PROMPT` đổi CTA thành kêu gọi
đăng ký kênh, `prompts/drama/rewriter.v2.txt` dặn `vn_commentary` kết bằng mời
bình luận + đăng ký kênh; (2) **guarantee tầng code** —
`video.text_preprocessor.ensure_subscribe_cta(text, cta)` soi ĐOẠN CUỐI
narration (cụm hẹp "đăng ký kênh"/"subscribe"/"theo dõi kênh" — "đăng ký
ChatGPT Plus" giữa bài không tính), thiếu thì nối câu CTA
(`AI_SUBSCRIBE_CTA`/`DRAMA_SUBSCRIBE_CTA`, env-overridable), có rồi thì không
đụng (không đọc 2 lần). Áp tại `main._create_video` (TRƯỚC khi lưu DB/TTS/
subtitle — một nguồn text nên audio và phụ đề luôn khớp) và
`main_drama.build_narration` (cứu cả story cũ rewrite trước khi prompt đổi).

**Composer:** phụ đề được gộp thành **một track trong suốt** (concat-demuxer →
overlay 1 lần) nên số input ffmpeg là hằng số, không phụ thuộc số dòng phụ đề.

**Chất lượng nền (background):** ba lớp cải tiến để nền bám nội dung và đỡ nhàm,
mà vẫn dùng nguồn Pexels miễn phí:
1. **B-roll terms theo nội dung:** `script_generator` để LLM trả thêm trường
   `broll_terms` (3-5 cụm tiếng Anh cụ thể, vd `"AI robot assistant"`). `main._extract_keywords`
   ưu tiên dùng các từ này để search Pexels (bám chủ đề hơn tiêu đề tiếng Việt);
   thiếu thì fallback heuristic cũ.
   **Pool nền đóng băng (fix 07/2026):** `get_background`/`get_backgrounds` cũ
   trả từ cache ngay khi BẤT KỲ query nào có cache — 5 query generic luôn được
   nối vào danh sách và cache từ ngày đầu, nên clip theo `broll_terms` KHÔNG
   BAO GIỜ được tải nữa → mọi AI video xoay quanh vài clip generic giống nhau,
   chẳng khớp nội dung. Fix 2 nửa: (a) **Pass 0** `_ensure_keyword_clips` tải
   tối đa `BG_KEYWORD_DOWNLOADS` (mặc định 2, 0 = hành vi cũ) clip MỚI cho
   keyword của chính bài chưa có cache mỗi video (tôn trọng sentinel lỗi
   auth/blocked của #97); (b) khi chọn, clip cache theo keyword CỦA BÀI thắng
   pool generic (content match), anti-repeat variety vẫn áp dụng.
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
