# Phase 2 — Drama Source Layer

> **Mục tiêu:** Xây tầng nguồn dữ liệu cho Drama track. Cuối phase, hệ thống có thể: (a) cào RSS từ subreddit drama, (b) nhận seed VN-original qua Telegram, (c) lưu tất cả vào bảng `stories` chờ chấm điểm.

**Thời lượng dự kiến:** 5–7 ngày.
**Phụ thuộc:** Phase 1 hoàn tất (cần `channels.py` + bảng `stories`).
**Khoá phase sau:** Phase 3 (Drama Generation Layer).

---

## 1. Bối cảnh

Strategy 2.0 yêu cầu Drama có 2 nguồn cân bằng 50/50:

- **Nguồn A — Reddit:** r/AmItheAsshole, r/AskReddit, r/relationship_advice, r/MaliciousCompliance, r/ProRevenge.
- **Nguồn B — VN-original:** "tình huống lõi" do Phuong feed thủ công qua Telegram bot, chỉ dùng để tạo cảm hứng (viết lại 100%).

Repo đã có `collectors/reddit_collector.py` cho AI track (r/ChatGPT, r/artificial). Phase 2 tách collector mới riêng cho Drama vì:

1. Subreddit khác → cấu hình khác.
2. Rule-filter khác (drama dùng upvote ngưỡng + length, không dùng keyword AI).
3. Schema lưu khác (`stories` chứ không phải `items`).

---

## 2. Phạm vi

### Trong phạm vi

- Collector mới `collectors/reddit_drama_collector.py`.
- Telegram command bot `notifier/seed_bot.py` (extend bot hiện có).
- Storage helper `storage/stories.py` (CRUD bảng `stories`).
- Rate-limit & dedupe (không lấy lại post đã có).
- Cron entry chạy collector mỗi sáng 6h.

### Ngoài phạm vi

- Logic chấm điểm + rewrite (Phase 3).
- Render video (Phase 4).

---

## 3. Thiết kế kỹ thuật

### 3.1 Reddit Drama Collector

```python
# content-pipeline/collectors/reddit_drama_collector.py

DRAMA_SUBREDDITS = [
    {"name": "AmItheAsshole", "min_upvotes": 5000, "weight": 1.5},
    {"name": "AskReddit",     "min_upvotes": 10000, "weight": 1.0},
    {"name": "relationship_advice", "min_upvotes": 3000, "weight": 1.3},
    {"name": "MaliciousCompliance", "min_upvotes": 5000, "weight": 1.4},
    {"name": "ProRevenge", "min_upvotes": 3000, "weight": 1.4},
]

# RSS endpoint: https://www.reddit.com/r/{name}/top/.rss?t=day
# Parse với feedparser, lấy trường: id, title, content (HTML), score (parse từ title hoặc post details API)
```

**Lưu ý:** RSS không trả `score`. Phải gọi thêm endpoint `https://www.reddit.com/r/{name}/comments/{post_id}.json` để lấy score và body đầy đủ. Đặt rate limit 1 request/2 giây.

**Output:** mỗi post đủ điều kiện được insert vào `stories` với:

```python
{
    "source": "reddit",
    "source_id": f"reddit_{post_id}",
    "raw_content": full_text,
    "track": "drama",
    "status": "pending",
    "metadata": json.dumps({"subreddit": ..., "upvotes": ..., "url": ...})
}
```

### 3.2 VN-original Seed Bot

Mở rộng `notifier/telegram_bot.py` thành 2 chế độ:
- **Notify mode** (đã có): bot push report sáng cho Phuong.
- **Receive mode** (mới): bot nhận command từ Phuong.

Commands:

| Command | Mô tả |
|---------|-------|
| `/seed_vn` | Bot trả lời "Hãy gửi tình huống lõi (1–3 câu)". Tin nhắn tiếp theo của Phuong sẽ được lưu vào `stories` với `source='vn_original'`. |
| `/seed_url` | Phuong paste link FB/TikTok. Bot lưu URL + caption làm raw content. |
| `/list_pending` | Bot trả về top 5 story đang chờ duyệt. |
| `/help` | Liệt kê command. |

**Thư viện:** dùng `python-telegram-bot==21.x`, mode webhook hoặc polling. Mac Mini chạy local nên polling đơn giản hơn.

### 3.3 Storage helper

```python
# content-pipeline/storage/stories.py

def insert_story(source, source_id, raw_content, track="drama", metadata=None):
    """Insert mới, raise IntegrityError nếu source_id trùng."""

def get_pending(limit=10, track=None):
    """Lấy story status='pending', sort theo created_at DESC."""

def update_status(story_id, status, **fields):
    """Cập nhật status + các field khác (rubric_score, rewritten_content...)."""

def dedupe_check(source_id) -> bool:
    """True nếu source_id đã tồn tại."""
```

### 3.4 Cron entry

Thêm vào `scripts/run_pipeline.sh`:

```bash
# 06:00 - cào Reddit Drama
06 06 * * * cd $REPO && python -m collectors.reddit_drama_collector >> logs/reddit_drama.log 2>&1
```

> Trên Mac Mini đang dùng `launchd`, cần tạo thêm `.plist` riêng cho job này.

---

## 4. Acceptance criteria

- [ ] `reddit_drama_collector.py` chạy được, insert ≥5 story/ngày từ 5 subreddit.
- [ ] Dedupe hoạt động (chạy 2 lần liên tiếp không tạo bản ghi trùng).
- [ ] Seed bot nhận `/seed_vn` và lưu story đúng format.
- [ ] `storage/stories.py` có ≥80% test coverage.
- [ ] Log riêng cho từng collector, rotation hằng ngày.
- [ ] Có integration test mock Reddit response.

---

## 5. Rủi ro & cảnh báo

- **Reddit rate limit:** Dùng RSS thay JSON API tối đa có thể (RSS không yêu cầu auth nếu < 100 req/min). Nếu cần JSON, đăng ký Reddit Script Application và dùng OAuth.
- **NSFW filter:** Nhiều story r/ProRevenge/r/AmItheAsshole có nội dung 18+. Thêm filter cờ `over_18` từ RSS, loại bỏ ngay.
- **Telegram bot crash:** Polling crash → mất data. Wrap trong systemd-style restart (launchd KeepAlive).
- **VN-original bản quyền:** Strategy nói "chỉ dùng cảm hứng". Đảm bảo rule này được nhắc trong prompt rewrite ở Phase 3 (không copy nguyên văn).

---

## 6. Liên kết

- Phase trước: [`phase-1-detailed.md`](phase-1-detailed.md)
- Phase tiếp theo: [`phase-3-detailed.md`](phase-3-detailed.md)
- Issues: [`phase-2-issues.md`](phase-2-issues.md)
