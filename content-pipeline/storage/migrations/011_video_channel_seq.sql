-- Migration 011: internal per-channel sequence number for videos.
-- Additive only: new nullable column + non-unique index. Legacy rows stay NULL
-- (never renumbered). The number is internal and never shown publicly.

ALTER TABLE videos ADD COLUMN channel_seq INTEGER;
CREATE INDEX IF NOT EXISTS idx_videos_track_channel_seq ON videos(track, channel_seq);
