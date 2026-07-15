-- Rollback migration 008: drop the pipeline_state kv table.
DROP TABLE IF EXISTS pipeline_state;
