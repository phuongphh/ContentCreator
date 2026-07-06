-- Migration 003: collector_health table (Phase 2 — Operational hardening).
--
-- Tracks the last successful run per collector (keyed by a short name, e.g.
-- 'reddit_drama') so a separate health-check job can alert via Telegram if a
-- collector hasn't succeeded in N days (cron/launchd stopped firing, an
-- uncaught crash, etc.) — see storage/collector_health.py.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping INSERT in one transaction.

CREATE TABLE IF NOT EXISTS collector_health (
  name TEXT PRIMARY KEY,
  last_success TIMESTAMP
);
