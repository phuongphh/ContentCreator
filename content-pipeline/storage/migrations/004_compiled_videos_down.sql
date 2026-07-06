-- Rollback for 004_compiled_videos.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping DELETE in one transaction.

DROP TABLE IF EXISTS compiled_videos;
