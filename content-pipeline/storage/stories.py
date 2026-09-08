from __future__ import annotations

"""
Storage helper cho bảng `stories` — CRUD cho Drama track (Phase 2).

Bảng `stories` được tạo ở migration 001_multi_track; cột `title`/`metadata`
và unique index trên `source_id` được thêm ở migration 002_stories_metadata
(xem storage/migrate.py). Chạy `python -m storage.migrate up` trước khi dùng
module này.
"""

import hashlib
import json
import logging
import sqlite3
import unicodedata
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.database import get_connection

logger = logging.getLogger(__name__)

# Cache theo đường dẫn DB (không phải biến bool đơn) để test dùng DB tạm khác
# nhau không thừa hưởng trạng thái của nhau.
_COLUMN_CACHE: dict[str, bool] = {}
_BACKFILL_DONE: dict[str, bool] = {}

# Columns update_status() is allowed to touch besides `status`, to avoid
# building a dynamic UPDATE from caller-controlled column names.
_UPDATABLE_FIELDS = {
    "rubric_score", "rewritten_content", "destination", "produced_at", "title",
}


# --- Dedupe theo NỘI DUNG (issue #120) ---
#
# Root cause #120: `source_id` mã hoá ĐƯỜNG NẠP (prefix importer + tên
# dataset), không phải nội dung. Cùng một bài Reddit AITA `9nlh04` vào kho 2
# lần dưới `aita_csv_9nlh04` (nạp CSV tay 15/07) và
# `hf_AITA-Reddit-Dataset_9nlh04` (importer HF 09/08) — dedupe theo source_id
# không thấy chúng là một, nên bản thứ hai vẫn nằm chờ và sẽ được render &
# đăng LẦN NỮA lên kênh drama. Fix ở tầng storage (không phải ở từng
# collector) để MỌI đường nạp — kể cả script tay sau này — đi qua cùng một
# chốt: importer đổi prefix, đổi dataset, hay dump chứa lại bài cũ đều bị bắt.
#
# Giới hạn đã biết: đây là dedupe TRÙNG KHỚP CHÍNH XÁC sau chuẩn hoá. Hai bản
# dump lệch nhau một dòng "EDIT:" thêm về sau vẫn lọt — bắt gần-đúng cần so
# từng cặp (O(n²)) hoặc chỉ mục MinHash, quá đắt so với lợi ích ở quy mô này.
_MIN_FINGERPRINT_CHARS = 32   # ngắn hơn mức này thì hash không đủ đặc trưng

# Ký tự "sang chảnh" hay khác nhau giữa các bản dump của cùng một bài
# (nháy cong, gạch dài, khoảng trắng không ngắt) — quy về ASCII trước khi hash.
_FINGERPRINT_TRANSLATION = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "′": "'", "″": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", "​": "", "‌": "", "‍": "", "﻿": "",
})


class DuplicateStoryError(sqlite3.IntegrityError):
    """Story trùng nội dung với một story đã có.

    Kế thừa `sqlite3.IntegrityError` có chủ đích: caller nào đang bắt
    IntegrityError cho ca "source_id trùng" (hợp đồng cũ của insert_story từ
    migration 002) tự động xử lý đúng ca trùng NỘI DUNG mà không phải sửa gì.
    """

    def __init__(self, existing_id: int, content_hash: str):
        super().__init__(
            f"story trùng nội dung với story #{existing_id} (content_hash={content_hash})"
        )
        self.existing_id = existing_id
        self.content_hash = content_hash


def content_fingerprint(text: Optional[str]) -> Optional[str]:
    """Vân tay nội dung của một story, hoặc None nếu text quá ngắn/rỗng.

    Chuẩn hoá trước khi băm để hai bản sao "cùng bài, khác nguồn" ra cùng một
    hash: NFKC → quy ký tự typographic về ASCII → casefold → gộp mọi khoảng
    trắng (dump khác nhau hay lệch xuống dòng/thụt lề).

    Trả None khi nội dung quá ngắn: một thân bài vài chục ký tự ("see title")
    có thể trùng nhau ở những bài KHÁC HẲN nhau — thà bỏ lọt còn hơn chặn oan
    story hợp lệ.
    """
    if not text:
        return None
    normalized = unicodedata.normalize("NFKC", str(text)).translate(_FINGERPRINT_TRANSLATION)
    normalized = " ".join(normalized.casefold().split())
    if len(normalized) < _MIN_FINGERPRINT_CHARS:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _has_content_hash_column(conn: sqlite3.Connection) -> bool:
    """True nếu migration 010 đã chạy trên DB này (cache theo đường dẫn DB).

    Suy giảm êm khi chưa migrate: dedupe nội dung tắt, mọi thứ chạy như trước
    thay vì sập cả collector — cùng cách xử lý cột `scheduled_posts.attempts`
    thiếu migration 009 (issue #109).
    """
    key = config.DB_PATH
    cached = _COLUMN_CACHE.get(key)
    if cached is None:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(stories)").fetchall()}
        cached = "content_hash" in cols
        _COLUMN_CACHE[key] = cached
        if not cached:
            logger.warning(
                "Bảng stories chưa có cột content_hash — dedupe theo nội dung bị "
                "TẮT. Chạy `python -m storage.migrate up` (migration 010, issue #120)."
            )
    return cached


def _ensure_hashes(conn: sqlite3.Connection) -> int:
    """Điền content_hash cho những story còn thiếu (một lần cho mỗi tiến trình).

    Chốt chặn chỉ hoạt động khi kho CŨ cũng có vân tay, mà SQLite không băm
    được — nên backfill phải chạy bằng Python. Đặt ở đây thay vì bắt caller
    nhớ gọi: mọi đường nạp đều đi qua insert_story/dedupe_check, nên kho luôn
    tự lành sau lần chạy đầu tiên (một lượt quét, ~vài chục ms cho vài nghìn
    story; lần sau chỉ tốn 1 câu SELECT có index).
    """
    key = config.DB_PATH
    if _BACKFILL_DONE.get(key):
        return 0
    if not _has_content_hash_column(conn):
        _BACKFILL_DONE[key] = True
        return 0
    rows = conn.execute(
        "SELECT id, raw_content FROM stories WHERE content_hash IS NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        fingerprint = content_fingerprint(_dedupe_source_text(row["raw_content"]))
        if fingerprint:
            conn.execute("UPDATE stories SET content_hash = ? WHERE id = ?",
                         (fingerprint, row["id"]))
            updated += 1
    if updated:
        conn.commit()
        logger.info("Backfill content_hash cho %d story (issue #120)", updated)
    _BACKFILL_DONE[key] = True
    return updated


def _dedupe_source_text(raw_content: Optional[str]) -> Optional[str]:
    """Phần dùng để băm từ `raw_content` — bỏ khối comment gắn thêm.

    `hf_drama_importer` nối "TOP COMMENTS FROM REDDIT" vào cuối raw_content
    (issue #92). Cùng một bài nạp có/không kèm comment phải ra CÙNG vân tay,
    nếu không thì mỗi lần đổi cấu hình comment là cả kho được nạp lại. Đường
    nạp mới nên truyền thẳng `dedupe_text=<thân bài>` cho insert_story; hàm này
    là lưới an toàn cho dữ liệu CŨ đã lưu kèm comment.
    """
    if not raw_content:
        return raw_content
    marker = "\n\n---\nTOP COMMENTS FROM REDDIT:"
    head, _, _ = raw_content.partition(marker)
    return head


def find_by_content(content_hash: Optional[str]) -> Optional[dict]:
    """Story cũ có cùng vân tay nội dung (bản CŨ NHẤT), None nếu chưa có."""
    if not content_hash:
        return None
    conn = get_connection()
    try:
        if not _has_content_hash_column(conn):
            return None
        _ensure_hashes(conn)
        row = conn.execute(
            "SELECT * FROM stories WHERE content_hash = ? ORDER BY id LIMIT 1",
            (content_hash,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def insert_story(source: str, source_id: Optional[str], raw_content: str,
                 track: str = "drama", title: Optional[str] = None,
                 metadata: Optional[dict] = None,
                 dedupe_text: Optional[str] = None) -> int:
    """Insert a new story with status='pending'. Returns the story id.

    Args:
        dedupe_text: phần THÂN story dùng để tính vân tay nội dung (issue
            #120). Mặc định lấy `raw_content` đã bỏ khối comment gắn thêm.
            Đường nạp nào có sẵn thân bài "sạch" (HF importer tách body khỏi
            comment) nên truyền vào để vân tay không đổi khi cấu hình comment
            đổi.

    Raises:
        DuplicateStoryError: nếu đã có story cùng nội dung (dù source_id khác
            hẳn) — chốt chặn cuối cho MỌI đường nạp, kể cả script tay.
        sqlite3.IntegrityError: nếu `source_id` đã tồn tại (unique index từ
            migration 002). Gọi `dedupe_check()` trước nếu muốn tránh raise.
    """
    fingerprint = content_fingerprint(
        dedupe_text if dedupe_text is not None else _dedupe_source_text(raw_content)
    )
    conn = get_connection()
    try:
        has_hash = _has_content_hash_column(conn)
        if has_hash and fingerprint:
            _ensure_hashes(conn)
            existing = conn.execute(
                "SELECT id FROM stories WHERE content_hash = ? ORDER BY id LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if existing:
                raise DuplicateStoryError(existing["id"], fingerprint)
        if has_hash:
            cursor = conn.execute(
                "INSERT INTO stories (source, source_id, raw_content, track, title, "
                "metadata, status, content_hash) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    source, source_id, raw_content, track, title,
                    json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
                    fingerprint,
                ),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO stories (source, source_id, raw_content, track, title, metadata, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (
                    source, source_id, raw_content, track, title,
                    json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
                ),
            )
        conn.commit()
        logger.info("Inserted story id=%d source=%s source_id=%s", cursor.lastrowid, source, source_id)
        return cursor.lastrowid
    finally:
        conn.close()


def dedupe_check(source_id: str, content: Optional[str] = None) -> bool:
    """True nếu story này đã có trong kho.

    Hai khoá, hai loại trùng KHÁC NHAU:
      * `source_id` — cùng một bài từ cùng một đường nạp (rẻ, chính xác).
      * `content` — cùng NỘI DUNG dù source_id khác hẳn (issue #120): cùng bài
        Reddit đi qua 2 importer, hay một dump chứa lại bài đã nạp.

    Caller nên truyền `content` = thân story (chưa nối comment/tiêu đề) khi có.
    """
    fingerprint = content_fingerprint(content) if content else None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM stories WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row is not None:
            return True
        if not fingerprint or not _has_content_hash_column(conn):
            return False
        _ensure_hashes(conn)
        dup = conn.execute(
            "SELECT id FROM stories WHERE content_hash = ? LIMIT 1", (fingerprint,)
        ).fetchone()
        if dup is not None:
            logger.info("Bỏ qua story trùng NỘI DUNG với story #%d (source_id mới=%s)",
                        dup["id"], source_id)
            return True
        return False
    finally:
        conn.close()


def get_story(story_id: int) -> Optional[dict]:
    """Lấy 1 story theo id."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_pending(limit: int = 10, track: Optional[str] = None) -> list[dict]:
    """Lấy story status='pending', sort theo created_at DESC.

    Xem `get_by_status()` — đây chỉ là shortcut cho status='pending' (dùng
    nhiều nhất: seed bot, scorer, rewriter).
    """
    return get_by_status("pending", limit=limit, track=track)


def get_by_status(status: str, limit: Optional[int] = 10,
                  track: Optional[str] = None) -> list[dict]:
    """Lấy story theo `status` bất kỳ, sort theo created_at DESC.

    Tie-broken bằng `id DESC`: SQLite's CURRENT_TIMESTAMP chỉ có độ chính xác
    tới giây, nên nhiều story insert trong cùng 1 giây (bình thường với 1 lần
    chạy collector) sẽ có `created_at` giống hệt nhau — dùng `id` (tăng dần
    theo thứ tự insert) làm tie-breaker để thứ tự luôn ổn định/đúng insert order.

    Args:
        status: 'pending', 'approved', 'rejected', 'needs_review', 'produced', ...
        limit: số story tối đa; ``None`` = KHÔNG giới hạn (SQLite ``LIMIT -1``).
            Dùng cho tác vụ quét-toàn-bộ như `drama_rewriter --revalidate`
            (review PR #100): sort DESC + limit khiến story CŨ không bao giờ
            lọt vào trang đầu khi tồn đọng nhiều hơn `limit`.
        track: lọc theo track ('drama', 'ai', ...) nếu truyền vào, mặc định
            lấy mọi track.
    """
    sql_limit = -1 if limit is None else limit
    conn = get_connection()
    try:
        if track:
            rows = conn.execute(
                "SELECT * FROM stories WHERE status = ? AND track = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, track, sql_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stories WHERE status = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, sql_limit),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def count_producible(track: str = "drama") -> int:
    """Số story còn có thể sản xuất (status 'pending' | 'approved') cho 1 track.

    Dùng cho cảnh báo backlog cạn (storage/collector_health.check_drama_backlog,
    issue #78): khi Reddit tắt, track Drama sống bằng seed thủ công, nên tín hiệu
    sức khoẻ đúng là "còn đủ story để sản xuất không", không phải "collector có
    chạy không". 'pending' = chờ chấm điểm/Việt hoá; 'approved' = chờ render.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM stories "
            "WHERE track = ? AND status IN ('pending', 'approved')",
            (track,),
        ).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


def update_status(story_id: int, status: str, **fields) -> None:
    """Cập nhật status + các field khác (rubric_score, rewritten_content, ...).

    Raises:
        ValueError: nếu `fields` chứa tên cột không nằm trong allowlist
            (`_UPDATABLE_FIELDS`) — tránh dựng UPDATE động từ tên cột tuỳ ý.
    """
    unknown = set(fields) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"update_status: unknown field(s) {sorted(unknown)}")

    set_clauses = ["status = ?"]
    params: list = [status]
    for key, value in fields.items():
        set_clauses.append(f"{key} = ?")
        params.append(value)
    params.append(story_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE stories SET {', '.join(set_clauses)} WHERE id = ?", params
        )
        conn.commit()
    finally:
        conn.close()


# --- Dọn story trùng đã tồn tại (issue #120) ---
#
# Chốt chặn ở insert_story chỉ chặn được từ hôm nay trở đi; kho hiện tại đã có
# cặp trùng (story 302 `aita_csv_9nlh04` chờ render lại nội dung mà story 961
# đã lên sóng thành video 202). Hàm dưới tìm và VÔ HIỆU HOÁ bản chưa dùng —
# mặc định chỉ BÁO CÁO, phải `--apply` mới ghi (dữ liệu sản xuất, không tự ý sửa).

# Thứ tự "đã đi xa tới đâu": bản đi xa nhất được GIỮ, các bản còn lại bị vô
# hiệu hoá. Story đã 'produced' luôn thắng — nó là bản đã thành video.
_STATUS_RANK = {"produced": 3, "approved": 2, "needs_review": 1, "pending": 0}
# Chỉ những status này mới còn có thể ra video → mới cần vô hiệu hoá. Bản
# 'rejected'/'duplicate' đã nằm ngoài dây chuyền rồi, đụng vào chỉ nhiễu.
_NEUTRALIZABLE = {"pending", "approved", "needs_review"}
DUPLICATE_STATUS = "duplicate"


def mark_duplicate(story_id: int, keeper_id: int) -> None:
    """Đưa story trùng ra khỏi dây chuyền, ghi lại nó là bản sao của story nào.

    Ghi `metadata.duplicate_of` bằng UPDATE tường minh (không đi qua
    `update_status`) để allowlist cột động của hàm đó giữ nguyên phạm vi hẹp.
    """
    conn = get_connection()
    try:
        row = conn.execute("SELECT metadata FROM stories WHERE id = ?",
                           (story_id,)).fetchone()
        meta = {}
        if row and row["metadata"]:
            try:
                meta = json.loads(row["metadata"]) or {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["duplicate_of"] = keeper_id
        conn.execute(
            "UPDATE stories SET status = ?, metadata = ? WHERE id = ?",
            (DUPLICATE_STATUS, json.dumps(meta, ensure_ascii=False), story_id),
        )
        conn.commit()
    finally:
        conn.close()


def find_duplicate_groups(track: Optional[str] = None) -> list[list[dict]]:
    """Các nhóm story cùng vân tay nội dung (≥2 bản), mỗi nhóm sort theo id."""
    conn = get_connection()
    try:
        if not _has_content_hash_column(conn):
            return []
        _ensure_hashes(conn)
        params: list = []
        where = "content_hash IS NOT NULL"
        if track:
            where += " AND track = ?"
            params.append(track)
        rows = conn.execute(
            f"SELECT id, status, content_hash, source_id, title FROM stories "
            f"WHERE {where} AND content_hash IN ("
            f"  SELECT content_hash FROM stories WHERE {where} "
            f"  GROUP BY content_hash HAVING COUNT(*) > 1) "
            f"ORDER BY content_hash, id",
            params + params,
        ).fetchall()
    finally:
        conn.close()

    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["content_hash"], []).append(dict(row))
    return list(groups.values())


def resolve_duplicates(track: Optional[str] = None, apply: bool = False) -> dict:
    """Vô hiệu hoá các bản sao chưa dùng. Trả báo cáo để in/test.

    Giữ bản đi xa nhất trong dây chuyền (produced > approved > needs_review >
    pending; hoà thì giữ bản CŨ hơn — nó đã được chấm điểm/ngắm nghía lâu hơn).
    Nhóm có ≥2 bản 'produced' nghĩa là ĐÃ đăng trùng thật: không tự sửa được
    nữa, chỉ nêu ra để người xử lý.
    """
    report = {"groups": 0, "marked": [], "already_produced": []}
    for group in find_duplicate_groups(track):
        report["groups"] += 1
        keeper = max(group, key=lambda r: (_STATUS_RANK.get(r["status"], -1), -r["id"]))
        produced = [r["id"] for r in group if r["status"] == "produced"]
        if len(produced) > 1:
            report["already_produced"].append(produced)
        for row in group:
            if row["id"] == keeper["id"] or row["status"] not in _NEUTRALIZABLE:
                continue
            report["marked"].append({"id": row["id"], "keeper": keeper["id"],
                                     "source_id": row["source_id"]})
            if apply:
                mark_duplicate(row["id"], keeper["id"])
    return report

def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    if d.get("metadata"):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass  # leave as raw string — malformed metadata shouldn't break callers
    return d


def _main() -> None:
    """CLI: `python -m storage.stories dedupe [--apply] [--track drama]`.

    Không tốn tiền AI, không đụng file video — chỉ đọc/ghi bảng `stories`.
    Chạy MỘT LẦN sau khi deploy migration 010 để dọn cặp trùng cũ (issue #120).
    """
    import argparse

    parser = argparse.ArgumentParser(description="Story storage utilities")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["status", "dedupe"])
    parser.add_argument("--track", default=None, help="lọc theo track (vd drama)")
    parser.add_argument("--apply", action="store_true",
                        help="thực sự ghi (mặc định chỉ báo cáo)")
    args = parser.parse_args()

    if args.command == "status":
        print("Pending stories:", len(get_pending(track=args.track)))
        return

    report = resolve_duplicates(track=args.track, apply=args.apply)
    print(f"Nhóm trùng nội dung: {report['groups']}")
    for item in report["marked"]:
        verb = "đã vô hiệu hoá" if args.apply else "SẼ vô hiệu hoá"
        print(f"  - story #{item['id']} ({item['source_id']}) {verb} "
              f"— trùng story #{item['keeper']}")
    for ids in report["already_produced"]:
        print(f"  ⚠️ ĐÃ đăng trùng (cần người xử lý): story {ids}")
    if not args.apply and report["marked"]:
        print("Chạy lại với --apply để ghi thay đổi.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _main()
