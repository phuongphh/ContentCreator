# Phase 2 — Issues Master List

> Drama Source Layer. 4 Epic, ~14 sub-issues, ~7 ngày.

---

## EPIC #2.1 — Reddit Drama Collector

**Loại:** `epic` `phase-2` `backend`
**Mô tả:** Cào Reddit RSS từ 5 subreddit drama, gọi JSON API để lấy upvote/body, lưu vào bảng `stories` với dedupe.

**Definition of Done:**
- Chạy được lệnh `python -m collectors.reddit_drama_collector` cho ra ≥5 story/ngày.
- Dedupe đúng (test idempotent).
- Có cron entry trên Mac Mini.

### Sub-issues

#### `[feat]` Tạo skeleton `reddit_drama_collector.py`
- **Labels:** `phase-2` `backend` `feat` `drama`
- **Estimate:** S
- **Mô tả:** Tạo file với constant `DRAMA_SUBREDDITS`, hàm `fetch_subreddit(name, min_upvotes)` placeholder.

#### `[feat]` Implement RSS parser cho subreddit
- **Labels:** `phase-2` `backend` `feat`
- **Estimate:** M
- **Phụ thuộc:** Issue trên
- **Mô tả:** Parse RSS với `feedparser`, lọc post `over_18=False`, trả về list `{id, title, link, summary}`.
- **Acceptance:**
  - [ ] Test với fixture RSS XML
  - [ ] Loại được NSFW

#### `[feat]` Implement post detail fetch (JSON API)
- **Labels:** `phase-2` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Gọi `/r/{name}/comments/{post_id}.json` để lấy `selftext` + `ups`. Rate limit 1 req/2s. Retry 3 lần với backoff.

#### `[feat]` Tích hợp dedupe + insert vào `stories`
- **Labels:** `phase-2` `backend` `feat` `database`
- **Estimate:** M
- **Phụ thuộc:** Storage helper (Epic 2.3)
- **Acceptance:**
  - [ ] Chạy 2 lần liên tiếp: lần 2 insert 0 row mới
  - [ ] Log rõ "skipped X duplicates"

#### `[infra]` Cron entry + launchd plist
- **Labels:** `phase-2` `infra` `task`
- **Estimate:** S
- **Mô tả:** Tạo `scripts/launchd/com.contentcreator.reddit-drama.plist`, lịch 06:00.

---

## EPIC #2.2 — VN-original Seed Bot (Telegram)

**Loại:** `epic` `phase-2` `backend` `bot`
**Mô tả:** Extend Telegram bot hiện có để nhận command, lưu seed VN từ Phuong.

**Definition of Done:**
- 4 command (`/seed_vn`, `/seed_url`, `/list_pending`, `/help`) chạy đúng.
- Bot tự khởi động lại khi crash.

### Sub-issues

#### `[feat]` Refactor `telegram_bot.py` thành 2 mode (notify + receive)
- **Labels:** `phase-2` `backend` `refactor`
- **Estimate:** M
- **Mô tả:** Tách thành 2 module: `telegram_notifier.py` (đã có, chỉ rename) và `telegram_seed_bot.py` (mới, polling mode).
- **Acceptance:**
  - [ ] Backward compatible: notify cũ vẫn chạy
  - [ ] 2 process độc lập

#### `[feat]` Implement command `/seed_vn`
- **Labels:** `phase-2` `backend` `feat` `drama`
- **Estimate:** M
- **Mô tả:** Khi nhận `/seed_vn`, bot phản hồi gợi ý format. Tin nhắn kế tiếp của user lưu vào `stories` với `source='vn_original'`. Dùng FSM (finite state machine) trong python-telegram-bot.

#### `[feat]` Implement command `/seed_url`
- **Labels:** `phase-2` `backend` `feat` `drama`
- **Estimate:** M
- **Mô tả:** Nhận URL FB/TikTok, fetch metadata (title, thumbnail), lưu vào `stories.metadata`. Dùng `httpx` + Open Graph parser.

#### `[feat]` Implement command `/list_pending`
- **Labels:** `phase-2` `backend` `feat`
- **Estimate:** S
- **Mô tả:** Trả top 5 story `status='pending'`, format markdown ngắn.

#### `[infra]` Bot daemon + auto-restart
- **Labels:** `phase-2` `infra`
- **Estimate:** S
- **Mô tả:** launchd plist với `KeepAlive=true`.

---

## EPIC #2.3 — Storage helper cho `stories`

**Loại:** `epic` `phase-2` `backend` `database`
**Mô tả:** CRUD module cho bảng `stories` (đã được tạo ở Phase 1).

### Sub-issues

#### `[feat]` Tạo `storage/stories.py` với 4 hàm cơ bản
- **Labels:** `phase-2` `database` `feat`
- **Estimate:** M
- **Mô tả:** `insert_story`, `get_pending`, `update_status`, `dedupe_check` như mô tả `phase-2-detailed.md`.
- **Acceptance:**
  - [ ] Type hint đầy đủ
  - [ ] Raise đúng exception khi trùng `source_id`

#### `[test]` Unit test cho `storage/stories.py`
- **Labels:** `phase-2` `test`
- **Estimate:** M
- **Mô tả:** Test với SQLite in-memory, ≥80% coverage.

---

## EPIC #2.4 — Operational hardening

**Loại:** `epic` `phase-2` `infra`
**Mô tả:** Logging, monitoring, alert nếu collector fail.

### Sub-issues

#### `[infra]` Setup logging rotation
- **Labels:** `phase-2` `infra`
- **Estimate:** S
- **Mô tả:** `logs/reddit_drama.log`, rotate hằng ngày, giữ 14 ngày. Dùng `logging.handlers.TimedRotatingFileHandler`.

#### `[feat]` Alert Telegram nếu collector fail 2 ngày liên tiếp
- **Labels:** `phase-2` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Sau mỗi run, ghi `last_success` vào `meta` table. Job riêng kiểm tra mỗi 12h, nếu `last_success < now - 2d` → push alert vào Telegram.

---

## Tóm tắt Phase 2

| Epic | Issues | Estimate |
|------|--------|----------|
| #2.1 Reddit Drama Collector | 5 | ~2.5 ngày |
| #2.2 VN Seed Bot | 5 | ~2.5 ngày |
| #2.3 Storage helper | 2 | ~1 ngày |
| #2.4 Hardening | 2 | ~1 ngày |
| **Tổng** | **14** | **~7 ngày** |
