-- Migration 004: compiled_videos table (Phase 3 EPIC #3.3 — Drama Compiler).
--
-- Stores the long-form script produced by processors/drama_compiler.py,
-- which gathers 3-5 same-theme `stories` (status='produced', i.e. already
-- turned into an individual video by Phase 4) into one 8-15 minute video.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping INSERT in one transaction.

CREATE TABLE IF NOT EXISTS compiled_videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  theme TEXT NOT NULL,
  story_ids TEXT NOT NULL,        -- JSON list of stories.id
  script TEXT,                    -- full_script, ready for TTS
  chapter_markers TEXT,           -- JSON list of "MM:SS Title" strings
  status TEXT DEFAULT 'draft',    -- draft, ready, produced
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
