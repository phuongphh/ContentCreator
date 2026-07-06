-- Rollback for 002_stories_metadata.
-- Requires SQLite >= 3.35.0 (2021-03-12) for ALTER TABLE ... DROP COLUMN.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping DELETE in one transaction.

DROP INDEX IF EXISTS idx_stories_source_id;
ALTER TABLE stories DROP COLUMN metadata;
ALTER TABLE stories DROP COLUMN title;
