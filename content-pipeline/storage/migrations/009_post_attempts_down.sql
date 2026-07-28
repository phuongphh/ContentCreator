-- Rollback for 009_post_attempts.
-- SQLite 3.35+ hỗ trợ DROP COLUMN; cột không nằm trong index nào nên drop sạch.

ALTER TABLE scheduled_posts DROP COLUMN attempts;
