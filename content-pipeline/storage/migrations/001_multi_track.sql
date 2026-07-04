-- Migration 001: multi-track foundation (Phase 1 — Multi-channel).
--
-- Adds `track` and `destination` to the existing `articles` and `videos`
-- tables so every content item can be routed to the right channel, and
-- creates the `stories` table used by the Drama track (Phase 2+).
--
-- Applied via: python -m storage.migrate up
-- Rollback:    storage/migrations/001_multi_track_down.sql
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping INSERT in one transaction, so
-- schema + version record are applied atomically.

ALTER TABLE articles ADD COLUMN track TEXT NOT NULL DEFAULT 'ai';
ALTER TABLE articles ADD COLUMN destination TEXT;
CREATE INDEX IF NOT EXISTS idx_articles_track ON articles(track);
CREATE INDEX IF NOT EXISTS idx_articles_destination ON articles(destination);

ALTER TABLE videos ADD COLUMN track TEXT NOT NULL DEFAULT 'ai';
ALTER TABLE videos ADD COLUMN destination TEXT;
CREATE INDEX IF NOT EXISTS idx_videos_track ON videos(track);
CREATE INDEX IF NOT EXISTS idx_videos_destination ON videos(destination);

CREATE TABLE IF NOT EXISTS stories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,           -- 'reddit', 'vn_original', 'manual'
  source_id TEXT,                 -- Reddit post_id, hoặc UUID cho VN
  raw_content TEXT,
  rewritten_content TEXT,
  track TEXT NOT NULL,            -- 'drama' (sau này có thể mở rộng)
  rubric_score INTEGER,
  status TEXT DEFAULT 'pending',  -- pending, approved, rejected, produced
  destination TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  produced_at TIMESTAMP
);
