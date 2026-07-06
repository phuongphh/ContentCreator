-- Migration 002: stories title/metadata + unique source_id (Phase 2 — Drama
-- Source Layer).
--
-- Adds fields the Drama collectors/seed bot need: a display `title` (used by
-- /list_pending and Reddit post titles) and a JSON `metadata` blob (subreddit,
-- upvotes, url, ...). `source_id` gets a UNIQUE index so insert_story can rely
-- on sqlite3.IntegrityError for dedupe instead of a separate existence check.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping INSERT in one transaction.

ALTER TABLE stories ADD COLUMN title TEXT;
ALTER TABLE stories ADD COLUMN metadata TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_stories_source_id ON stories(source_id);
