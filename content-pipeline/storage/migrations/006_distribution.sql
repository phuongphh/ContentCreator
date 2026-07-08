-- Migration 006: distribution layer (Phase 5 — Distribution & Multi-channel Upload).
--
-- 1. `scheduled_posts` — hàng đợi upload theo cadence (scheduler/post_scheduler.py).
--    Mỗi row là "1 video → 1 kênh → 1 giờ đăng". Trạng thái đi 1 chiều:
--    queued → uploading → done|failed. `platform_video_id` được lưu NGAY khi
--    upload thành công để chống upload trùng khi pipeline restart giữa chừng
--    (rủi ro "Upload trùng" trong phase-5-detailed.md mục 5).
-- 2. `quota_usage` — đếm unit YouTube Data API đã dùng mỗi ngày (upload ~1600
--    unit, set thumbnail ~50) để alert khi chạm 80% quota (storage/quota.py).
-- 3. Cột mới trên `videos`:
--    - story_id: nối video Drama về stories (resume-from-crash: không render
--      lại story đã có video).
--    - thumbnail_path: file thumbnail upload riêng qua thumbnails().set().
--    - review_note: lý do reject từ review gate (notifier/review_bot.py).
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping INSERT in one transaction.

CREATE TABLE IF NOT EXISTS scheduled_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER NOT NULL,
  channel_key TEXT NOT NULL,          -- khoá trong channels.py
  scheduled_at TEXT NOT NULL,         -- 'YYYY-MM-DD HH:MM:SS' giờ local (so sánh lexicographic được)
  status TEXT NOT NULL DEFAULT 'queued',  -- queued | uploading | done | failed | cancelled
  platform_video_id TEXT,             -- YouTube video id / TikTok publish_id, lưu ngay khi upload OK
  url TEXT,
  error TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scheduled_posts_due ON scheduled_posts(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_posts_video ON scheduled_posts(video_id);
-- 1 slot (kênh + giờ) chỉ có 1 post đang hoạt động — scheduler tự tìm slot
-- trống, index này là chốt chặn cuối chống double-book do race/bug.
CREATE UNIQUE INDEX IF NOT EXISTS ux_scheduled_posts_slot
  ON scheduled_posts(channel_key, scheduled_at)
  WHERE status IN ('queued', 'uploading');
-- 1 video chỉ được xếp lịch 1 lần cho mỗi kênh (trừ khi lần trước failed/cancelled).
CREATE UNIQUE INDEX IF NOT EXISTS ux_scheduled_posts_video_channel
  ON scheduled_posts(video_id, channel_key)
  WHERE status IN ('queued', 'uploading', 'done');

CREATE TABLE IF NOT EXISTS quota_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service TEXT NOT NULL,              -- 'youtube'
  date TEXT NOT NULL,                 -- YYYY-MM-DD theo giờ Pacific (YouTube quota reset nửa đêm PT)
  units INTEGER NOT NULL,
  note TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_quota_usage_service_date ON quota_usage(service, date);

ALTER TABLE videos ADD COLUMN story_id INTEGER;
ALTER TABLE videos ADD COLUMN thumbnail_path TEXT;
ALTER TABLE videos ADD COLUMN review_note TEXT;
CREATE INDEX IF NOT EXISTS idx_videos_story ON videos(story_id);
