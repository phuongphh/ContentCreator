-- Migration 005: ab_runs table (Phase 3 EPIC #3.4 — Prompt versioning & A/B harness).
--
-- Records which prompt version was used for a given story + experiment, and
-- a heuristic quality score for it, so processors/ab_harness.py can compare
-- versions after enough samples accumulate.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping INSERT in one transaction.

CREATE TABLE IF NOT EXISTS ab_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment TEXT NOT NULL,
  version TEXT NOT NULL,
  story_id INTEGER,
  heuristic_score REAL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ab_runs_experiment ON ab_runs(experiment);
