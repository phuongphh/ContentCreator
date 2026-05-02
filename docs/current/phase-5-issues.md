# Phase 5 — Issues Master List

> Distribution. 4 Epic, ~15 sub-issues, ~7 ngày.

---

## EPIC #5.1 — Telegram Review Gate

**Loại:** `epic` `phase-5` `backend` `bot`
**Mô tả:** Bot review video preview với accept/reject/edit-metadata.

**Definition of Done:**
- 1 video đi qua review gate end-to-end (push, accept, schedule).

### Sub-issues

#### `[feat]` Tạo `notifier/review_bot.py`
- **Labels:** `phase-5` `backend` `feat`
- **Estimate:** L
- **Mô tả:** Push video preview <50MB. Inline keyboard 3 nút (✅/❌/✏️). Callback handler.

#### `[feat]` Edit metadata flow (FSM)
- **Labels:** `phase-5` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Nhấn ✏️ → bot hỏi field nào cần sửa → user nhập → update DB.

#### `[feat]` Video compress để fit Telegram <50MB
- **Labels:** `phase-5` `backend` `feat`
- **Estimate:** S
- **Mô tả:** FFmpeg lower bitrate cho preview, không ảnh hưởng file gốc.

---

## EPIC #5.2 — YouTube Multi-channel Upload

**Loại:** `epic` `phase-5` `backend` `upload`
**Mô tả:** Upload tới 2 kênh YouTube khác nhau dựa trên `channel_key`.

### Sub-issues

#### `[infra]` OAuth flow setup cho 2 kênh
- **Labels:** `phase-5` `infra` `oauth`
- **Estimate:** M
- **Mô tả:** Script CLI `scripts/oauth_init.py {channel_key}` chạy 1 lần để lấy refresh token. Lưu vào `tokens/{channel_key}.json`. Document trong `docs/current/oauth-setup.md`.
- **Acceptance:**
  - [ ] Token cho `ai_youtube` lấy được
  - [ ] Token cho `drama_youtube` lấy được
  - [ ] Token gitignored

#### `[feat]` Tạo `uploaders/youtube_uploader.py`
- **Labels:** `phase-5` `backend` `feat`
- **Estimate:** L
- **Mô tả:** Hàm `upload_to_youtube(video_id, channel_key)`. Resumable upload, retry với exponential backoff, lưu `youtube_video_id` ngay khi success.

#### `[feat]` Thumbnail upload riêng
- **Labels:** `phase-5` `backend` `feat`
- **Estimate:** S
- **Mô tả:** Sau khi upload video xong, nếu có thumbnail file → gọi `thumbnails().set()`.

#### `[feat]` Quota tracking
- **Labels:** `phase-5` `backend` `observability`
- **Estimate:** S
- **Mô tả:** Track unit consumption per Google Cloud Project, alert nếu >80% daily quota.

#### `[test]` E2E upload test (private mode)
- **Labels:** `phase-5` `test`
- **Estimate:** M
- **Mô tả:** Upload 1 video lên mỗi kênh ở privacy=`unlisted` cho test, verify metadata, xoá sau.

---

## EPIC #5.3 — TikTok Uploader (manual + API)

**Loại:** `epic` `phase-5` `backend` `upload`
**Mô tả:** Manual queue + API uploader (defer-able).

### Sub-issues

#### `[feat]` Manual export queue
- **Labels:** `phase-5` `backend` `feat` `mvp`
- **Estimate:** S
- **Mô tả:** Hàm `export_for_manual_upload(video_id)`. Copy MP4 + tạo `.txt` kèm caption + hashtag vào `queue_tiktok/YYYY-MM-DD/`.

#### `[task]` Đăng ký TikTok Developer App
- **Labels:** `phase-5` `task` `external`
- **Estimate:** M
- **Mô tả:** Tạo app trên TikTok Developer Portal, request product "Content Posting API". Có thể mất 2–4 tuần approval.

#### `[feat]` `uploaders/tiktok_uploader_api.py`
- **Labels:** `phase-5` `backend` `feat` `defer-able`
- **Estimate:** L
- **Phụ thuộc:** Issue đăng ký TikTok app
- **Mô tả:** OAuth flow + 3-step upload (init/chunks/publish). Test với account dev trước.

---

## EPIC #5.4 — Scheduler & Orchestrator

**Loại:** `epic` `phase-5` `backend` `infra`
**Mô tả:** Queue cadence + orchestrator end-to-end.

### Sub-issues

#### `[feat]` `scheduler/post_scheduler.py`
- **Labels:** `phase-5` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Đọc `CADENCE`, queue video vào slot kế tiếp. Bảng DB `scheduled_posts(video_id, channel_key, scheduled_at, status)`.

#### `[feat]` Cron runner mỗi 5 phút
- **Labels:** `phase-5` `infra` `feat`
- **Estimate:** S
- **Mô tả:** launchd plist chạy `python -m scheduler.post_scheduler tick` mỗi 5 phút.

#### `[feat]` `main_drama.py` orchestrator
- **Labels:** `phase-5` `backend` `feat`
- **Estimate:** M
- **Mô tả:** Pipeline đầu-đến-cuối Drama: collect → score → rewrite → render → review → schedule. Mỗi bước có resume-from-crash.

#### `[infra]` Health check endpoint
- **Labels:** `phase-5` `infra` `feat`
- **Estimate:** S
- **Mô tả:** Mini HTTP server local trả `/health` với trạng thái từng module (last_run, error count). Telegram daily report kéo từ đây.

---

## Tóm tắt Phase 5

| Epic | Issues | Estimate |
|------|--------|----------|
| #5.1 Review gate | 3 | ~2 ngày |
| #5.2 YouTube multi-channel | 5 | ~3 ngày |
| #5.3 TikTok | 3 | ~1 ngày (manual) + defer API |
| #5.4 Scheduler | 4 | ~2 ngày |
| **Tổng** | **15** | **~7 ngày** (chưa tính API TikTok) |
