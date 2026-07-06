-- Rollback for 005_ab_runs.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping DELETE in one transaction.

DROP INDEX IF EXISTS idx_ab_runs_experiment;
DROP TABLE IF EXISTS ab_runs;
