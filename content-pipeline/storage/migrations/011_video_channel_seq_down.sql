-- Rollback for 011_video_channel_seq.
-- Requires SQLite >= 3.35.0 for ALTER TABLE ... DROP COLUMN.

DROP INDEX IF EXISTS idx_videos_track_channel_seq;
ALTER TABLE videos DROP COLUMN channel_seq;
