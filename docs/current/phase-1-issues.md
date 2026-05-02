# Phase 1 — Issues Master List

> Mục lục các Epic + sub-issues của Phase 1. File này là **bản nháp** trước khi bạn duyệt và push lên GitHub. Sau khi push, GitHub Action `issue-lifecycle.yml` sẽ tự sinh file riêng cho mỗi issue trong `docs/issues/active/`.
>
> **Convention:** Mỗi Epic có 4–8 sub-issues. Sub-issue có tiền tố loại `[feat]`, `[task]`, `[chore]`, `[docs]`, `[infra]`. Ước lượng theo S/M/L (S ≤ 0.5 ngày, M = 0.5–1.5 ngày, L = 2–3 ngày).

---

## EPIC #1.1 — Channel registry & config refactor

**Loại:** `epic` `phase-1` `backend`
**Mô tả:** Tạo `channels.py` registry và refactor toàn bộ tham chiếu hard-code kênh trong codebase hiện tại sang gọi qua registry. Là khoá nền cho tất cả phase sau.

**Definition of Done:**
- `channels.py` được merge.
- `config.py` không còn chuỗi `'youtube'` hard-code làm channel.
- Test cũ pass với track mặc định `ai`.

### Sub-issues

#### `[feat]` Tạo `content-pipeline/channels.py` với 3 channel cơ bản
- **Labels:** `phase-1` `backend` `feat`
- **Estimate:** S
- **Mô tả:** Tạo file registry với 3 entry (`ai_youtube`, `drama_youtube`, `tiktok_main`) như mô tả trong `phase-1-detailed.md` mục 3.1.
- **Acceptance:**
  - [ ] File tồn tại với type hint
  - [ ] Function `get_channel(key)` raise `ValueError` nếu key sai
  - [ ] Có unit test ở `tests/test_channels.py`

#### `[chore]` Refactor `config.py` để dùng channel registry
- **Labels:** `phase-1` `backend` `refactor`
- **Estimate:** M
- **Phụ thuộc:** Issue tạo `channels.py` (ở trên)
- **Mô tả:** Tìm mọi tham chiếu hard-code 'youtube'/'tiktok' trong `config.py` và các module khác, thay bằng `from channels import CHANNELS, get_channel`.
- **Acceptance:**
  - [ ] `grep -r "youtube\|tiktok" content-pipeline/` không còn hard-code chuỗi destination
  - [ ] Tất cả module hiện có vẫn import được

#### `[infra]` Cập nhật `.env.example`
- **Labels:** `phase-1` `infra` `docs`
- **Estimate:** S
- **Mô tả:** Thêm các biến env mới: `YOUTUBE_AI_TOKEN`, `YOUTUBE_AI_CHANNEL_ID`, `YOUTUBE_DRAMA_TOKEN`, `YOUTUBE_DRAMA_CHANNEL_ID`, `TIKTOK_TOKEN`, `TIKTOK_OPEN_ID`. Có comment giải thích cách lấy.
- **Acceptance:**
  - [ ] Mỗi biến có ghi chú nguồn (Google Cloud Console / TikTok Developer Portal)

---

## EPIC #1.2 — DB migration đa track

**Loại:** `epic` `phase-1` `database`
**Mô tả:** Cập nhật schema SQLite để mọi item có `track` và `destination`. Tạo bảng `stories` cho Drama (Phase 2 sẽ dùng).

**Definition of Done:**
- Migration 001 chạy được idempotent.
- Có rollback script.
- Schema mới được document trong `CLAUDE.md`.

### Sub-issues

#### `[feat]` Viết migration `001_multi_track.sql`
- **Labels:** `phase-1` `database` `feat`
- **Estimate:** M
- **Mô tả:** Theo mô tả ở `phase-1-detailed.md` mục 3.2. Bao gồm `ALTER TABLE items` và `CREATE TABLE stories`. Wrap trong transaction.
- **Acceptance:**
  - [ ] Chạy 2 lần không lỗi (dùng `IF NOT EXISTS` hoặc check schema_version)
  - [ ] Có rollback `001_multi_track_down.sql`

#### `[feat]` Migration runner `storage/migrate.py`
- **Labels:** `phase-1` `database` `feat`
- **Estimate:** M
- **Mô tả:** Script CLI đọc thư mục `migrations/`, áp dụng theo thứ tự, lưu version vào bảng `_migrations`.
- **Acceptance:**
  - [ ] Lệnh `python -m storage.migrate up` chạy được
  - [ ] Lệnh `python -m storage.migrate status` in danh sách migration đã/chưa apply
  - [ ] Test integration với DB tạm

#### `[docs]` Cập nhật `CLAUDE.md` schema section
- **Labels:** `phase-1` `docs`
- **Estimate:** S

---

## EPIC #1.3 — Branding 2 kênh YouTube + TikTok

**Loại:** `epic` `phase-1` `branding`
**Mô tả:** Tạo 2 kênh YouTube + 1 TikTok với branding asset đầy đủ. Đây là task thủ công (không code).

**Definition of Done:**
- 2 kênh YouTube live, có ≥3 video placeholder hoặc trailer.
- 1 TikTok account live với bio có 2 hashtag series.
- Asset folder `branding/` được commit vào repo (logo, banner, channel description text).

### Sub-issues

#### `[task]` Đặt tên 2 kênh + 1 TikTok account
- **Labels:** `phase-1` `branding` `decision`
- **Estimate:** S
- **Mô tả:** Brainstorm 5 tên cho mỗi kênh, chốt 1. Check tên không trùng trên YouTube/TikTok. Mua domain ngắn nếu cần (tuỳ chọn).
- **Acceptance:**
  - [ ] Có file `branding/naming.md` ghi tên cuối + lý do

#### `[task]` Thiết kế logo + banner cho kênh AI
- **Labels:** `phase-1` `branding` `design`
- **Estimate:** M
- **Mô tả:** Avatar 800×800, banner 2560×1440, có thể dùng Midjourney/Ideogram + Canva.
- **Acceptance:**
  - [ ] File trong `branding/ai_youtube/avatar.png`, `banner.png`
  - [ ] Tải lên YouTube studio xong

#### `[task]` Thiết kế logo + banner cho kênh Drama
- **Labels:** `phase-1` `branding` `design`
- **Estimate:** M

#### `[task]` Tạo TikTok account + bio + linktree
- **Labels:** `phase-1` `branding` `task`
- **Estimate:** S
- **Acceptance:**
  - [ ] Bio chứa 2 hashtag series + link bio (Linktree/Beacons)

#### `[task]` Viết description 2 kênh YouTube
- **Labels:** `phase-1` `branding` `copy`
- **Estimate:** S
- **Mô tả:** Mỗi kênh 200–400 ký tự, có keyword chính + lịch đăng + CTA.

#### `[task]` Setup Brand Account YouTube cho 2 kênh
- **Labels:** `phase-1` `infra` `task`
- **Estimate:** M
- **Mô tả:** Tạo Brand Account riêng để OAuth không cần dùng Gmail cá nhân làm owner trực tiếp. Document quy trình trong `docs/current/oauth-setup.md`.

---

## EPIC #1.4 — Documentation update

**Loại:** `epic` `phase-1` `docs`
**Mô tả:** Cập nhật mọi tài liệu để phản ánh kiến trúc multi-track.

### Sub-issues

#### `[docs]` Cập nhật root `README.md`
- **Estimate:** S
- **Mô tả:** Thêm section "Multi-channel architecture" giải thích track/destination.

#### `[docs]` Cập nhật `CLAUDE.md`
- **Estimate:** S
- **Mô tả:** Cập nhật mục "Kiến trúc thư mục" + thêm "Channel registry".

#### `[docs]` Viết `docs/current/oauth-setup.md`
- **Estimate:** M
- **Mô tả:** Hướng dẫn lấy OAuth token cho YouTube Data API v3 cho từng Brand Account và TikTok Developer Portal cho TikTok Content Posting API. Có screenshot.

---

## Tóm tắt Phase 1

| Epic | Issues | Estimate tổng |
|------|--------|---------------|
| #1.1 Channel registry | 3 | ~1.5 ngày |
| #1.2 DB migration | 3 | ~1.5 ngày |
| #1.3 Branding | 6 | ~3 ngày (manual heavy) |
| #1.4 Docs | 3 | ~1 ngày |
| **Tổng** | **15** | **~7 ngày** |
