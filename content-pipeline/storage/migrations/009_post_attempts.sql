-- Migration 009: đếm số lần thử upload của 1 post (issue #109).
--
-- Bối cảnh: token OAuth của ai_youtube hết hạn giữa hai lần kiểm tra (probe
-- 08:00 báo OK, upload 12:00 chết vì invalid_grant). Post bị mark_failed và
-- KHÔNG có đường quay lại — cấp lại token xong video vẫn nằm im.
--
-- RefreshError xảy ra TRƯỚC khi gửi byte nào lên YouTube nên requeue post đó
-- an toàn tuyệt đối (khác post kẹt 'uploading' giữa chừng — có thể đã lên sóng,
-- vẫn KHÔNG bao giờ tự retry). `attempts` chặn vòng lặp vô hạn: quá
-- config.POST_AUTH_RETRY_MAX lần thì mark_failed như cũ.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents
-- together with the _migrations bookkeeping INSERT in one transaction.

ALTER TABLE scheduled_posts ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
