-- Migration 010: stories.content_hash — dedupe theo NỘI DUNG (issue #120).
--
-- Root cause #120: `source_id` là khoá dedupe DUY NHẤT, mà nó mã hoá ĐƯỜNG
-- NẠP chứ không phải nội dung ("aita_csv_9nlh04" vs "hf_AITA-Reddit-Dataset_
-- 9nlh04" là CÙNG một bài Reddit 9nlh04, nạp 2 đợt bằng 2 importer). Đổi
-- dataset/prefix/importer là cả kho được nạp lại lần nữa, và story trùng nằm
-- chờ tới lượt render → nguy cơ đăng TRÙNG narrative lên kênh drama.
--
-- `content_hash` = SHA-256 của phần THÂN story sau chuẩn hoá (xem
-- storage/stories.content_fingerprint), nên hai bản sao cùng nội dung khớp
-- nhau dù source_id/nguồn/ngày nạp khác hẳn.
--
-- Index KHÔNG unique có chủ đích: (1) DB hiện tại ĐÃ có cặp trùng (story 302
-- và 961) nên UNIQUE sẽ làm migration chết ngay khi tạo index; (2) ta muốn
-- GIỮ hàng trùng cũ để truy vết, chỉ đổi status của bản chưa dùng. Chốt chặn
-- ghi nằm ở insert_story (raise DuplicateStoryError) — cùng tinh thần
-- "predicate một nguồn" của issue #117.
--
-- No BEGIN/COMMIT here — storage/migrate.py wraps this file's contents together
-- with the _migrations bookkeeping INSERT in one transaction.

ALTER TABLE stories ADD COLUMN content_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_stories_content_hash ON stories(content_hash);
