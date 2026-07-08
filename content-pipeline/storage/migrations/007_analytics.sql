-- Migration 007: analytics layer (Phase 6 — Analytics & Iteration).
--
-- 1. `video_metrics` — snapshot số liệu 1 video trên 1 platform tại 1 ngày
--    (analytics/youtube_puller.py, analytics/tiktok_csv.py). Snapshot mỗi 24h.
-- 2. `channel_metrics` — snapshot cấp KÊNH (subscriber, view) để tính sub
--    growth cho weekly retro — video_metrics một mình không suy ra được số
--    subscriber của cả kênh.
-- 3. `cost_logs` — token/chi phí mỗi call AI (Anthropic) + dịch vụ ngoài
--    (Replicate/TTS). Phase-6-detailed.md giả định bảng này "đã ghi từ Phase
--    3/4" nhưng thực tế processors/ai_usage.py trước đây chỉ log ra Python
--    logging, KHÔNG persist. Migration này hiện thực hoá bảng đó; ai_usage
--    được nối dây (best-effort) để ghi vào đây. Lưu TOKEN THÔ (không phải $)
--    — quy đổi ra tiền là 1 lớp overlay ở analytics/pricing.py, cập nhật giá
--    không cần đụng dữ liệu lịch sử (giữ tinh thần ai_usage.py: token thô
--    không bao giờ "stale").
-- 4. Cột mới trên `videos`: experiment_id / experiment_arm — gắn 1 video vào
--    1 nhánh thí nghiệm A/B (thumbnail/hook/length), so sánh ở
--    analytics/experiment_compare.py.
--
-- Khác phase-6-issues.md (đặt tên `002_metrics_schema.sql`): repo đã dùng dãy
-- số migration liên tục tới 006, nên tiếp tục 007 thay vì reset về 002 (tránh
-- đụng version với 002_stories_metadata đã tồn tại).
--
-- Khác phase-6-detailed.md (`video_metrics.video_id NOT NULL`, `UNIQUE(video_id,
-- snapshot_at)`): video_id để NULLABLE vì metric TikTok giai đoạn manual (CSV
-- từ TikTok Studio) không có đường map đáng tin về `videos.id` — ép NOT NULL
-- đồng nghĩa vứt toàn bộ số liệu TikTok. Khoá upsert thật là
-- (platform, external_id, snapshot_date); video_id là FK best-effort điền khi
-- biết (map qua scheduled_posts.platform_video_id).
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping INSERT in one transaction.

CREATE TABLE IF NOT EXISTS video_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER,                    -- FK videos.id, NULL nếu chưa map được (TikTok manual)
  platform TEXT NOT NULL,              -- 'youtube' | 'tiktok'
  external_id TEXT NOT NULL,           -- youtube_video_id / tiktok video id
  channel_key TEXT,                    -- khoá trong channels.py (kênh nguồn của số liệu)
  snapshot_date TEXT NOT NULL,         -- 'YYYY-MM-DD' (1 snapshot/ngày)
  views INTEGER,
  likes INTEGER,
  comments INTEGER,
  shares INTEGER,
  watch_time_minutes REAL,
  avg_view_duration_seconds REAL,
  retention_50_pct REAL,               -- % người xem tới giữa video
  ctr REAL,                            -- click-through rate (YouTube impressions)
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Khoá upsert: cùng platform+video+ngày chỉ giữ 1 snapshot (ghi đè trong ngày).
CREATE UNIQUE INDEX IF NOT EXISTS ux_video_metrics_snapshot
  ON video_metrics(platform, external_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_video_metrics_video ON video_metrics(video_id);
CREATE INDEX IF NOT EXISTS idx_video_metrics_snapshot ON video_metrics(snapshot_date);

CREATE TABLE IF NOT EXISTS channel_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_key TEXT NOT NULL,           -- khoá trong channels.py
  platform TEXT NOT NULL,              -- 'youtube' | 'tiktok'
  snapshot_date TEXT NOT NULL,
  subscribers INTEGER,                 -- tổng subscriber tại thời điểm snapshot (nếu API trả)
  subscribers_gained INTEGER,          -- subscriber tăng trong kỳ (retro dùng cho sub growth)
  views INTEGER,                       -- view của kênh trong kỳ
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_channel_metrics_snapshot
  ON channel_metrics(channel_key, snapshot_date);

CREATE TABLE IF NOT EXISTS cost_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service TEXT NOT NULL,               -- 'anthropic' | 'replicate' | 'tts' | ...
  model TEXT,                          -- vd 'claude-haiku-4-5' (để pricing overlay quy đổi $)
  label TEXT,                          -- bước gọi (drama_scorer, drama_rewriter, ...)
  input_tokens INTEGER,
  output_tokens INTEGER,
  units REAL,                          -- đơn vị phi-token (giây TTS, ảnh Replicate) nếu có
  ref_type TEXT,                       -- 'story' | 'article' | 'video' (loại id tham chiếu)
  ref_id TEXT,
  date TEXT NOT NULL,                  -- 'YYYY-MM-DD' (giờ local) để tổng hợp theo ngày
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cost_logs_date ON cost_logs(date);
CREATE INDEX IF NOT EXISTS idx_cost_logs_service ON cost_logs(service);

ALTER TABLE videos ADD COLUMN experiment_id TEXT;
ALTER TABLE videos ADD COLUMN experiment_arm TEXT;
CREATE INDEX IF NOT EXISTS idx_videos_experiment ON videos(experiment_id);
