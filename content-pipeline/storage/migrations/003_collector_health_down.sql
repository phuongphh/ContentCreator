-- Rollback for 003_collector_health.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping DELETE in one transaction.

DROP TABLE IF EXISTS collector_health;
