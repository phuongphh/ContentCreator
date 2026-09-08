"""Tests dedupe theo nội dung cho bảng stories (issue #120).

Ca thật của issue: bài Reddit AITA `9nlh04` vào kho 2 lần dưới
`aita_csv_9nlh04` (nạp CSV tay) và `hf_AITA-Reddit-Dataset_9nlh04` (importer
HF). Dedupe theo source_id không thấy chúng là một → bản thứ hai nằm chờ và
sẽ được render & đăng LẦN NỮA lên kênh drama.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.database as db
import storage.migrate as migrate
import storage.stories as stories

BODY = (
    "AITA for not feeling capable of listening to the details of my mother's "
    "childhood abuse? She keeps bringing it up at dinner and I freeze every time."
)


class _StoryDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbpath = os.path.join(self.tmp, "test.db")
        self._patch = patch.object(db.config, "DB_PATH", self.dbpath)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        # Cache cột/backfill khoá theo đường dẫn DB — dọn để test không thừa
        # hưởng trạng thái của nhau.
        stories._COLUMN_CACHE.clear()
        stories._BACKFILL_DONE.clear()
        self.addCleanup(stories._COLUMN_CACHE.clear)
        self.addCleanup(stories._BACKFILL_DONE.clear)
        db.init_db()
        migrate.migrate_up()


class TestFingerprint(unittest.TestCase):
    def test_stable_across_formatting_differences(self):
        """Hai bản dump cùng bài hay lệch xuống dòng/hoa thường/nháy cong."""
        a = stories.content_fingerprint(BODY)
        b = stories.content_fingerprint(BODY.upper().replace("'", "’").replace(" ", "\n  "))
        self.assertEqual(a, b)

    def test_different_stories_differ(self):
        self.assertNotEqual(stories.content_fingerprint(BODY),
                            stories.content_fingerprint(BODY + " Chuyện khác hẳn ở đây."))

    def test_too_short_has_no_fingerprint(self):
        """Thân bài vài chữ có thể trùng nhau ở những bài KHÁC HẲN — thà bỏ
        lọt còn hơn chặn oan."""
        self.assertIsNone(stories.content_fingerprint("xem tiêu đề"))
        self.assertIsNone(stories.content_fingerprint(""))
        self.assertIsNone(stories.content_fingerprint(None))

    def test_appended_comments_do_not_change_identity(self):
        """raw_content cũ có gắn khối comment (issue #92) vẫn cùng vân tay với
        thân bài — nếu không, đổi cấu hình comment là nạp lại cả kho."""
        with_comments = BODY + "\n\n---\nTOP COMMENTS FROM REDDIT:\n- YTA, obviously"
        self.assertEqual(stories.content_fingerprint(BODY),
                         stories.content_fingerprint(stories._dedupe_source_text(with_comments)))


class TestCrossImporterDedupe(_StoryDBTest):
    def test_same_reddit_post_via_two_importers_is_one_story(self):
        """Đúng ca issue #120: 2 source_id, 1 bài."""
        stories.insert_story(source="huggingface", source_id="aita_csv_9nlh04",
                             raw_content=BODY, track="drama", dedupe_text=BODY)
        self.assertTrue(stories.dedupe_check("hf_AITA-Reddit-Dataset_9nlh04", content=BODY))

    def test_insert_refuses_duplicate_even_without_dedupe_check(self):
        """Chốt chặn cuối: script tay quên gọi dedupe_check vẫn không nhét
        được bản trùng vào kho."""
        first = stories.insert_story(source="huggingface", source_id="aita_csv_9nlh04",
                                     raw_content=BODY, track="drama")
        with self.assertRaises(stories.DuplicateStoryError) as ctx:
            stories.insert_story(source="huggingface", source_id="hf_ds_9nlh04",
                                 raw_content=BODY, track="drama")
        self.assertEqual(ctx.exception.existing_id, first)
        # Kế thừa IntegrityError → caller bắt kiểu cũ vẫn xử lý đúng.
        self.assertIsInstance(ctx.exception, sqlite3.IntegrityError)

    def test_comment_enriched_copy_dedupes_against_plain_copy(self):
        stories.insert_story(source="gsheet", source_id="csv_1", raw_content=BODY,
                             track="drama", dedupe_text=BODY)
        enriched = BODY + "\n\n---\nTOP COMMENTS FROM REDDIT:\n- NTA"
        self.assertTrue(stories.dedupe_check("hf_1", content=BODY))
        with self.assertRaises(stories.DuplicateStoryError):
            stories.insert_story(source="huggingface", source_id="hf_1",
                                 raw_content=enriched, track="drama", dedupe_text=BODY)

    def test_distinct_stories_still_pass(self):
        stories.insert_story(source="lemmy", source_id="l1", raw_content=BODY, track="drama")
        other = BODY.replace("mother's", "neighbour's") + " Và rồi mọi chuyện vỡ lở."
        self.assertFalse(stories.dedupe_check("l2", content=other))
        self.assertTrue(stories.insert_story(source="lemmy", source_id="l2",
                                             raw_content=other, track="drama"))


class TestLegacyRows(_StoryDBTest):
    def _null_out_hashes(self):
        conn = sqlite3.connect(self.dbpath)
        conn.execute("UPDATE stories SET content_hash = NULL")
        conn.commit()
        conn.close()
        stories._BACKFILL_DONE.clear()

    def test_backfill_makes_old_rows_dedupable(self):
        """Story nạp TRƯỚC migration 010 vẫn phải chặn được bản trùng mới."""
        stories.insert_story(source="huggingface", source_id="aita_csv_9nlh04",
                             raw_content=BODY, track="drama")
        self._null_out_hashes()
        self.assertTrue(stories.dedupe_check("hf_ds_9nlh04", content=BODY))

    def test_degrades_quietly_without_migration_010(self):
        """Chưa chạy migration → dedupe nội dung TẮT, pipeline vẫn chạy."""
        conn = sqlite3.connect(self.dbpath)
        conn.execute("DROP INDEX IF EXISTS idx_stories_content_hash")
        conn.execute("ALTER TABLE stories DROP COLUMN content_hash")
        conn.commit()
        conn.close()
        stories._COLUMN_CACHE.clear()
        stories._BACKFILL_DONE.clear()
        stories.insert_story(source="a", source_id="s1", raw_content=BODY, track="drama")
        self.assertFalse(stories.dedupe_check("s2", content=BODY))
        self.assertTrue(stories.insert_story(source="b", source_id="s2",
                                             raw_content=BODY, track="drama"))


class TestResolveDuplicates(_StoryDBTest):
    def _seed_pair(self):
        old_id = stories.insert_story(source="huggingface", source_id="aita_csv_9nlh04",
                                      raw_content=BODY, track="drama")
        # Bản thứ 2 mô phỏng dữ liệu CŨ (lọt vào trước khi có chốt chặn).
        conn = sqlite3.connect(self.dbpath)
        conn.execute(
            "INSERT INTO stories (source, source_id, raw_content, track, status, content_hash) "
            "VALUES ('huggingface', 'hf_AITA-Reddit-Dataset_9nlh04', ?, 'drama', "
            "'produced', ?)",
            (BODY, stories.content_fingerprint(BODY)),
        )
        produced_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return old_id, produced_id

    def test_dry_run_reports_without_writing(self):
        pending_id, _ = self._seed_pair()
        report = stories.resolve_duplicates(track="drama")
        self.assertEqual(report["groups"], 1)
        self.assertEqual([m["id"] for m in report["marked"]], [pending_id])
        self.assertEqual(stories.get_story(pending_id)["status"], "pending")

    def test_apply_neutralises_the_unused_copy(self):
        """Bản CHƯA dùng bị đưa khỏi dây chuyền; bản ĐÃ lên sóng giữ nguyên."""
        pending_id, produced_id = self._seed_pair()
        stories.resolve_duplicates(track="drama", apply=True)
        marked = stories.get_story(pending_id)
        self.assertEqual(marked["status"], stories.DUPLICATE_STATUS)
        self.assertEqual(marked["metadata"]["duplicate_of"], produced_id)
        self.assertEqual(stories.get_story(produced_id)["status"], "produced")
        # Không còn được tính là "có thể sản xuất" → không thể đăng trùng.
        self.assertEqual(stories.count_producible("drama"), 0)

    def test_flags_groups_already_published_twice(self):
        """2 bản cùng 'produced' = đã đăng trùng thật — code không sửa được
        nữa, phải nêu ra cho người xử lý."""
        _, produced_id = self._seed_pair()
        stories.update_status(produced_id, "produced")
        conn = sqlite3.connect(self.dbpath)
        conn.execute("UPDATE stories SET status = 'produced' WHERE status = 'pending'")
        conn.commit()
        conn.close()
        report = stories.resolve_duplicates(track="drama", apply=True)
        self.assertEqual(len(report["already_produced"]), 1)
        self.assertEqual(report["marked"], [])   # không đụng story đã lên sóng

    def test_no_duplicates_no_work(self):
        stories.insert_story(source="a", source_id="s1", raw_content=BODY, track="drama")
        self.assertEqual(stories.resolve_duplicates(track="drama"),
                         {"groups": 0, "marked": [], "already_produced": []})


if __name__ == "__main__":
    unittest.main()
