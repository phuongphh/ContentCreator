-- Rollback for 006_distribution.
-- SQLite (3.35+) hỗ trợ DROP COLUMN; các index trên bảng bị drop sẽ tự biến mất.

DROP INDEX IF EXISTS idx_videos_story;
ALTER TABLE videos DROP COLUMN review_note;
ALTER TABLE videos DROP COLUMN thumbnail_path;
ALTER TABLE videos DROP COLUMN story_id;

DROP TABLE IF EXISTS quota_usage;
DROP TABLE IF EXISTS scheduled_posts;
