-- Migration 011: per-channel creation-order sequence number for videos.
-- Nullable, no backfill: rows existing before this migration keep NULL.
-- Internal only; never sent to YouTube.
ALTER TABLE videos ADD COLUMN channel_seq INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS ux_videos_track_channel_seq
  ON videos(track, channel_seq)
  WHERE channel_seq IS NOT NULL;
