-- Rollback for 001_multi_track.
-- Requires SQLite >= 3.35.0 (2021-03-12) for ALTER TABLE ... DROP COLUMN.

BEGIN TRANSACTION;

DROP TABLE IF EXISTS stories;

DROP INDEX IF EXISTS idx_videos_destination;
DROP INDEX IF EXISTS idx_videos_track;
ALTER TABLE videos DROP COLUMN destination;
ALTER TABLE videos DROP COLUMN track;

DROP INDEX IF EXISTS idx_articles_destination;
DROP INDEX IF EXISTS idx_articles_track;
ALTER TABLE articles DROP COLUMN destination;
ALTER TABLE articles DROP COLUMN track;

COMMIT;
