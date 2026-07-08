-- Rollback for 007_analytics.
-- SQLite (3.35+) hỗ trợ DROP COLUMN; các index trên bảng bị drop tự biến mất.

DROP INDEX IF EXISTS idx_videos_experiment;
ALTER TABLE videos DROP COLUMN experiment_arm;
ALTER TABLE videos DROP COLUMN experiment_id;

DROP TABLE IF EXISTS video_metrics;
DROP TABLE IF EXISTS channel_metrics;
DROP TABLE IF EXISTS cost_logs;
