-- Migration 008: pipeline_state — a tiny generic key/value store (issue #90).
--
-- First user: the daily HuggingFace drama import (collectors/hf_drama_importer.py
-- import_daily) persists a per-dataset offset cursor here, so each day's run
-- walks FORWARD through the 270K-row static AITA dump instead of re-importing the
-- same tail (the flaw of the old `--newest` daily path). With Reddit off (#78)
-- and Lemmy drama communities near-empty, this cursor turns the static dump into
-- a reliable daily drama source and fixes the "0 videos" outcome of issue #90.
--
-- Generic on purpose: any collector/job that needs to remember a small scalar
-- across runs (a cursor, a last-seen id, a timestamp) can reuse this instead of
-- adding a bespoke table or a fragile state file (state files don't survive a
-- re-clone; the DB does — the same durability the drama pipeline already relies
-- on for resume-from-crash).
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents together
-- with the _migrations bookkeeping INSERT in one transaction.

CREATE TABLE IF NOT EXISTS pipeline_state (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
