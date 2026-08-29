from __future__ import annotations

import math
import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """Get a database connection."""
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT UNIQUE,
                raw_content TEXT,
                summary TEXT,
                ai_score REAL,
                ai_analysis TEXT,
                category TEXT,
                urgency TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_type TEXT NOT NULL,
                script_text TEXT NOT NULL,
                audio_path TEXT,
                subtitle_path TEXT,
                video_path TEXT,
                youtube_title TEXT,
                youtube_description TEXT,
                tiktok_caption TEXT,
                tiktok_hashtags TEXT,
                status TEXT DEFAULT 'draft',
                scheduled_date TEXT,
                scheduled_platform TEXT,
                telegram_message_id TEXT,
                approved_at TIMESTAMP,
                published_at TIMESTAMP,
                publish_url TEXT,
                subtitles_burned INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migration: add subtitles_burned to pre-existing DBs (NULL = unknown,
        # so legacy rows fall back to config inference at publish time).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
        if "subtitles_burned" not in cols:
            conn.execute("ALTER TABLE videos ADD COLUMN subtitles_burned INTEGER")
        # Indexes for frequently queried columns
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_ai_score ON articles(ai_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_created_at ON articles(created_at)")
        # (status, ai_score) phục vụ đúng hình dạng truy vấn chọn bài chấm điểm/
        # phân tích sâu — bảng articles lớn dần mỗi ngày (issue #117).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_status_score ON articles(status, ai_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_scheduled ON videos(scheduled_date, scheduled_platform)")
        conn.commit()
        logger.info("Database initialized successfully.")
    finally:
        conn.close()


def article_exists(url: str) -> bool:
    """Check if an article with the given URL already exists."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone()
        return row is not None
    finally:
        conn.close()


def insert_article(source: str, title: str, url: str,
                   raw_content: Optional[str] = None,
                   summary: Optional[str] = None,
                   track: str = "ai",
                   destination: Optional[str] = None) -> Optional[int]:
    """Insert a new article. Returns the article id or None if duplicate.

    `track`/`destination` require migration 001_multi_track (see
    storage/migrate.py); on a pre-migration DB the extra columns don't exist
    and this call falls back to the legacy INSERT below.
    """
    if article_exists(url):
        logger.debug("Article already exists: %s", url)
        return None
    conn = get_connection()
    try:
        try:
            cursor = conn.execute(
                "INSERT INTO articles (source, title, url, raw_content, summary, track, destination) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source, title, url, raw_content, summary, track, destination),
            )
        except sqlite3.OperationalError:
            cursor = conn.execute(
                "INSERT INTO articles (source, title, url, raw_content, summary) VALUES (?, ?, ?, ?, ?)",
                (source, title, url, raw_content, summary),
            )
        conn.commit()
        logger.info("Inserted article: %s", title[:80])
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        logger.debug("Duplicate article URL: %s", url)
        return None
    finally:
        conn.close()


def get_pending_articles(limit: int = 50) -> list[dict]:
    """Get articles that haven't been scored yet."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE ai_score IS NULL AND status = 'pending' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_score(article_id: int, score: float):
    """Update the AI score for an article."""
    conn = get_connection()
    try:
        conn.execute("UPDATE articles SET ai_score = ? WHERE id = ?", (score, article_id))
        conn.commit()
    finally:
        conn.close()


# --- Nội dung bài viết đủ để phân tích sâu (issue #117) ---
#
# Một bài chỉ có TIÊU ĐỀ không thể viết brief video trung thực — bắt Sonnet
# phân tích nó = bịa nội dung. Nên "có nội dung dùng được" là điều kiện chung
# cho cả tầng thu thập (collector không lưu entry rỗng), tầng chọn bài
# (get_articles_for_analysis) và tầng dọn dẹp (mark_articles_unanalyzable).
#
# Predicate tồn tại ở 2 dạng — Python (`has_usable_content`) và SQL
# (`_usable_content_sql`) — vì SQLite không gọi được hàm Python trong WHERE.
# Hai dạng PHẢI đồng thuận: tests/test_article_selection.py kiểm chứng trên
# cùng bộ dữ liệu mẫu (khoảng trắng, unicode, NULL) để chúng không trôi lệch.
#
# SQLite TRIM() mặc định CHỈ cắt dấu cách, còn str.strip() cắt mọi whitespace →
# liệt kê tường minh các ký tự trắng cho khớp Python.
_SQL_WHITESPACE = "' ' || CHAR(9) || CHAR(10) || CHAR(13) || CHAR(11) || CHAR(12)"


def _min_content_chars() -> int:
    """Ngưỡng ký tự tối thiểu (đọc lúc gọi để env/test override có hiệu lực)."""
    try:
        return max(1, int(getattr(config, "MIN_ARTICLE_CONTENT_CHARS", 30)))
    except (TypeError, ValueError):
        return 30


def _usable_content_sql() -> str:
    """Mảnh WHERE tương đương `has_usable_content` (dùng LENGTH ký tự, không byte)."""
    def _len(col: str) -> str:
        return f"LENGTH(TRIM(COALESCE({col}, ''), {_SQL_WHITESPACE}))"
    # MAX(a, b) của SQLite là hàm scalar khi có ≥2 tham số (không phải aggregate).
    return f"MAX({_len('raw_content')}, {_len('summary')}) >= {_min_content_chars()}"


def choose_article_content(raw_content: Optional[str], summary: Optional[str]) -> str:
    """Đoạn nội dung tốt nhất để phân tích: field DÀI HƠN sau khi strip.

    Bản cũ (`raw_content or summary`) chọn raw_content kể cả khi nó là mẩu cụt
    ngắn hơn summary — vừa mất thông tin, vừa lệch với predicate SQL.
    """
    raw = (raw_content or "").strip()
    summ = (summary or "").strip()
    return raw if len(raw) >= len(summ) else summ


def has_usable_content(raw_content: Optional[str], summary: Optional[str]) -> bool:
    """True nếu bài đủ nội dung để phân tích sâu / lưu vào DB."""
    return len(choose_article_content(raw_content, summary)) >= _min_content_chars()


def get_articles_for_analysis(threshold: float, limit: int = 5) -> list[dict]:
    """Get top-scored, ANALYSABLE articles that haven't been analyzed yet.

    Chỉ trả bài có nội dung dùng được (`has_usable_content`) và xếp theo
    **decayed_score** — cùng công thức `get_top_analyzed_articles`/
    `get_report_articles` dùng, nên bài được phân tích sâu chính là bài sẽ được
    chọn làm video (không đốt token Sonnet cho tin cũ mà decay sẽ loại sau đó).

    Ưu tiên bài đạt `threshold`; thiếu thì backfill bằng bài điểm thấp hơn.

    **Issue #117 — head-of-line blocking.** Bản cũ xếp thuần `ai_score DESC` và
    KHÔNG lọc theo nội dung: bài không có `raw_content` lẫn `summary` (RSS entry
    chỉ có tiêu đề) vẫn chiếm slot, bị `ai_analyzer` bỏ qua nhưng KHÔNG bao giờ
    rời khỏi pool (`status` vẫn 'pending', `ai_analysis` vẫn NULL) → hôm sau lại
    được chọn. Khi số bài rỗng ≥ MAX_DEEP_ANALYSIS (SQLite phá hoà bằng rowid
    tăng dần nên bài CŨ luôn thắng bài mới cùng điểm), 10/10 slot bị chiếm vĩnh
    viễn → `Analyzed 0/10` → 0 video, mỗi ngày, cho tới khi có người sửa tay.
    Hai lớp chặn: (1) SQL loại bài không có nội dung; (2) decay khiến bài cũ tự
    tụt hạng, nên MỌI loại bài kẹt (kể cả bài Sonnet parse hỏng dai dẳng) đều
    hết khả năng độc chiếm pool sau vài ngày.
    """
    conn = get_connection()
    try:
        # Lấy pool rộng hơn limit rồi mới decay-rank trong Python (exp() không
        # có trong SQLite) — cùng thủ thuật buffer ×4 của get_top_analyzed_articles.
        pool_size = limit * 4
        rows = conn.execute(
            "SELECT * FROM articles "
            "WHERE ai_score >= ? AND ai_analysis IS NULL AND status = 'pending' "
            f"AND {_usable_content_sql()} "
            "ORDER BY ai_score DESC LIMIT ?",
            (threshold, pool_size),
        ).fetchall()
        candidates = [dict(r) for r in rows]

        # Backfill: chưa đủ thì lấy tiếp bài dưới ngưỡng (ai_score < threshold
        # nên không bao giờ trùng với truy vấn trên — không cần dedupe tay).
        if len(candidates) < limit:
            backfill_rows = conn.execute(
                "SELECT * FROM articles "
                "WHERE ai_score IS NOT NULL AND ai_score < ? "
                "AND ai_analysis IS NULL AND status = 'pending' "
                f"AND {_usable_content_sql()} "
                "ORDER BY ai_score DESC LIMIT ?",
                (threshold, pool_size),
            ).fetchall()
            backfill = [dict(r) for r in backfill_rows]
        else:
            backfill = []
    finally:
        conn.close()

    def _rank(articles: list[dict]) -> list[dict]:
        for article in articles:
            article["decayed_score"] = _decayed_score(article)
        articles.sort(key=lambda a: a["decayed_score"], reverse=True)
        return articles

    result = _rank(candidates)[:limit]
    if len(result) < limit:
        result += _rank(backfill)[: limit - len(result)]
    return result


def mark_articles_unanalyzable() -> int:
    """Đưa bài KHÔNG THỂ phân tích sâu ra khỏi pool ('skipped'). Trả số bài đã đánh dấu.

    Bài đã chấm điểm nhưng không có nội dung (chỉ tiêu đề) sẽ không bao giờ
    phân tích được: viết brief video từ mỗi tiêu đề = bịa nội dung, nên bỏ hẳn
    thay vì giữ lại chờ đợi. Một câu UPDATE duy nhất (không vòng lặp Python) nên
    dọn được toàn bộ backlog tồn đọng trong 1 lần chạy, kể cả khi backlog lớn
    hơn MAX_DEEP_ANALYSIS.

    Đây là lớp *dọn dẹp* của issue #117 (bổ sung cho bộ lọc trong
    get_articles_for_analysis): pool nhỏ lại → truy vấn nhanh hơn và
    `count_analysis_candidates` phản ánh đúng thực tế còn dùng được.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE articles SET status = 'skipped' "
            "WHERE status = 'pending' AND ai_score IS NOT NULL "
            "AND ai_analysis IS NULL "
            f"AND NOT ({_usable_content_sql()})"
        )
        conn.commit()
        count = cursor.rowcount or 0
        if count:
            logger.info("Marked %d unanalyzable article(s) as skipped", count)
        return count
    finally:
        conn.close()


def count_analysis_candidates(threshold: float | None = None) -> dict:
    """Số liệu chẩn đoán cho bước phân tích sâu (1 truy vấn).

    Trả `{"pending_scored", "usable", "above_threshold"}` — dùng để giải thích
    "vì sao hôm nay 0 video" trong pipeline summary thay vì để chủ kênh tự đọc
    log (issue #117: pipeline im lặng 2 ngày).
    """
    if threshold is None:
        threshold = getattr(config, "SCORE_THRESHOLD_ANALYSIS", 5.5)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS pending_scored, "
            f"SUM(CASE WHEN {_usable_content_sql()} THEN 1 ELSE 0 END) AS usable, "
            f"SUM(CASE WHEN {_usable_content_sql()} AND ai_score >= ? THEN 1 ELSE 0 END) "
            "AS above_threshold "
            "FROM articles "
            "WHERE status = 'pending' AND ai_score IS NOT NULL AND ai_analysis IS NULL",
            (threshold,),
        ).fetchone()
        return {
            "pending_scored": row["pending_scored"] or 0,
            "usable": row["usable"] or 0,
            "above_threshold": row["above_threshold"] or 0,
        }
    finally:
        conn.close()


def update_analysis(article_id: int, analysis: dict):
    """Update the AI analysis for an article."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE articles SET ai_analysis = ?, category = ?, urgency = ? WHERE id = ?",
            (json.dumps(analysis, ensure_ascii=False),
             analysis.get("category"),
             analysis.get("urgency"),
             article_id),
        )
        conn.commit()
    finally:
        conn.close()


def _decayed_score(article: dict) -> float:
    """Compute time-decayed score for an article.

    final_score = ai_score × exp(-decay_rate × days_old)
    Minimum decay factor: 0.05 (bài >2 tuần vẫn có thể xuất hiện nếu điểm rất cao).
    Raw ai_score trong DB không thay đổi — decay chỉ áp dụng khi sắp xếp/lọc.
    """
    base = article.get("ai_score") or 0.0
    created_at_str = article.get("created_at") or ""
    if not created_at_str:
        return base

    try:
        # SQLite stores as "YYYY-MM-DD HH:MM:SS[.fff]" — parse with or without microseconds
        fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in created_at_str else "%Y-%m-%d %H:%M:%S"
        created_at = datetime.strptime(created_at_str, fmt).replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400
        days_old = max(0.0, days_old)
    except (ValueError, OverflowError):
        return base

    decay_rate = getattr(config, "SCORE_DECAY_RATE", 0.23)
    factor = max(0.05, math.exp(-decay_rate * days_old))
    return base * factor


def get_report_articles(score_threshold_notify: float) -> dict:
    """Get articles grouped by urgency for the daily report.

    Lọc và sắp xếp theo điểm sau khi áp dụng time decay.
    Ngưỡng threshold được so với decayed_score (không phải raw ai_score),
    đảm bảo bài cũ điểm cao vẫn bị lọc ra khi đã quá cũ.
    """
    conn = get_connection()
    try:
        # Lấy rộng hơn threshold một chút để bù cho decay
        rows = conn.execute(
            "SELECT * FROM articles WHERE ai_score IS NOT NULL AND ai_analysis IS NOT NULL "
            "AND status = 'pending' ORDER BY ai_score DESC",
        ).fetchall()
        articles = [dict(r) for r in rows]
    finally:
        conn.close()

    # Áp dụng time decay và lọc theo threshold
    result = {"immediate": [], "this_week": [], "backlog": []}
    for article in articles:
        article["decayed_score"] = _decayed_score(article)
        if article["decayed_score"] < score_threshold_notify:
            continue
        urgency = article.get("urgency", "backlog")
        if urgency in result:
            result[urgency].append(article)
        else:
            result["backlog"].append(article)

    # Sắp xếp mỗi nhóm theo decayed_score giảm dần
    for key in result:
        result[key].sort(key=lambda a: a["decayed_score"], reverse=True)

    return result


def get_top_analyzed_articles(limit: int = 5) -> list[dict]:
    """Get top N analyzed articles sorted by time-decayed score.

    Sắp xếp theo decayed_score thay vì raw ai_score để ưu tiên bài mới.
    Raw ai_score trong DB không thay đổi.
    """
    conn = get_connection()
    try:
        # Lấy nhiều hơn limit để sau khi decay vẫn đủ top N
        rows = conn.execute(
            "SELECT * FROM articles WHERE ai_analysis IS NOT NULL AND status = 'pending' "
            "ORDER BY ai_score DESC LIMIT ?",
            (limit * 4,),  # lấy buffer để bù cho bài cũ bị tụt hạng sau decay
        ).fetchall()
        articles = [dict(r) for r in rows]
    finally:
        conn.close()

    # Tính decayed_score và sắp xếp lại
    for article in articles:
        article["decayed_score"] = _decayed_score(article)
    articles.sort(key=lambda a: a["decayed_score"], reverse=True)

    return articles[:limit]


def mark_article_skipped(article_id: int):
    """Đưa 1 bài ra khỏi mọi pool xử lý ('skipped') — dùng khi bài không thể dùng.

    Cùng trạng thái mà `processors/rule_filter.py` dùng cho bài không liên quan,
    nên không sinh thêm khái niệm mới trong schema.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE articles SET status = 'skipped' WHERE id = ?", (article_id,)
        )
        conn.commit()
    finally:
        conn.close()


def mark_article_used(article_id: int):
    """Mark an article as used."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE articles SET status = 'used', used_at = ? WHERE id = ?",
            (datetime.now().isoformat(), article_id),
        )
        conn.commit()
    finally:
        conn.close()


# --- Video CRUD ---

def insert_video(video_type: str, script_text: str, youtube_title: str = "",
                 youtube_description: str = "", tiktok_caption: str = "",
                 tiktok_hashtags: str = "", scheduled_date: str = "",
                 scheduled_platform: str = "", track: str = "ai",
                 destination: Optional[str] = None,
                 story_id: Optional[int] = None) -> int:
    """Insert a new video record. Returns the video id.

    `track`/`destination` require migration 001_multi_track, `story_id`
    requires 006_distribution (see storage/migrate.py); on a pre-migration DB
    the extra columns don't exist and this call falls back to the legacy
    INSERT below.
    """
    conn = get_connection()
    try:
        try:
            cursor = conn.execute(
                "INSERT INTO videos (video_type, script_text, youtube_title, youtube_description, "
                "tiktok_caption, tiktok_hashtags, scheduled_date, scheduled_platform, track, destination, story_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (video_type, script_text, youtube_title, youtube_description,
                 tiktok_caption, tiktok_hashtags, scheduled_date, scheduled_platform,
                 track, destination, story_id),
            )
        except sqlite3.OperationalError:
            # DB has migration 001 (track/destination) but not 006 (story_id)?
            try:
                cursor = conn.execute(
                    "INSERT INTO videos (video_type, script_text, youtube_title, youtube_description, "
                    "tiktok_caption, tiktok_hashtags, scheduled_date, scheduled_platform, track, destination) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (video_type, script_text, youtube_title, youtube_description,
                     tiktok_caption, tiktok_hashtags, scheduled_date, scheduled_platform,
                     track, destination),
                )
            except sqlite3.OperationalError:
                cursor = conn.execute(
                    "INSERT INTO videos (video_type, script_text, youtube_title, youtube_description, "
                    "tiktok_caption, tiktok_hashtags, scheduled_date, scheduled_platform) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (video_type, script_text, youtube_title, youtube_description,
                     tiktok_caption, tiktok_hashtags, scheduled_date, scheduled_platform),
                )
        conn.commit()
        logger.info("Inserted video id=%d type=%s", cursor.lastrowid, video_type)
        return cursor.lastrowid
    finally:
        conn.close()


def update_video_paths(video_id: int, audio_path: str = None,
                       subtitle_path: str = None, video_path: str = None):
    """Update file paths for a video."""
    conn = get_connection()
    try:
        updates, params = [], []
        if audio_path is not None:
            updates.append("audio_path = ?")
            params.append(audio_path)
        if subtitle_path is not None:
            updates.append("subtitle_path = ?")
            params.append(subtitle_path)
        if video_path is not None:
            updates.append("video_path = ?")
            params.append(video_path)
        if updates:
            params.append(video_id)
            conn.execute(f"UPDATE videos SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
    finally:
        conn.close()


def update_video_status(video_id: int, status: str):
    """Update video status: draft, ready, pending_approval, approved, published, rejected."""
    conn = get_connection()
    try:
        extra = ""
        if status == "approved":
            extra = ", approved_at = CURRENT_TIMESTAMP"
        elif status == "published":
            extra = ", published_at = CURRENT_TIMESTAMP"
        conn.execute(f"UPDATE videos SET status = ?{extra} WHERE id = ?", (status, video_id))
        conn.commit()
    finally:
        conn.close()


def claim_video_status(video_id: int, new_status: str, expected_status: str) -> bool:
    """Atomically move a video from *expected_status* to *new_status*.

    Returns True only if THIS call performed the transition (exactly one row
    matched). Prevents races where two reviewers (Telegram + Web UI) both
    approve the same pending video and trigger duplicate publishes.
    """
    conn = get_connection()
    try:
        extra = ""
        if new_status == "approved":
            extra = ", approved_at = CURRENT_TIMESTAMP"
        elif new_status == "published":
            extra = ", published_at = CURRENT_TIMESTAMP"
        cur = conn.execute(
            f"UPDATE videos SET status = ?{extra} WHERE id = ? AND status = ?",
            (new_status, video_id, expected_status),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def update_video_telegram_id(video_id: int, message_id: str):
    """Store Telegram message ID for approval tracking."""
    conn = get_connection()
    try:
        conn.execute("UPDATE videos SET telegram_message_id = ? WHERE id = ?",
                     (message_id, video_id))
        conn.commit()
    finally:
        conn.close()


def update_video_publish_url(video_id: int, url: str):
    """Store the published URL after upload."""
    conn = get_connection()
    try:
        conn.execute("UPDATE videos SET publish_url = ? WHERE id = ?", (url, video_id))
        conn.commit()
    finally:
        conn.close()


def set_video_subtitles_burned(video_id: int, burned: bool):
    """Record whether subtitles were hard-burned into this video at render time.

    Persisted so publish-time logic (caption-track upload) uses the decision made
    when the MP4 was rendered, not whatever BURN_SUBTITLES happens to be later.
    """
    conn = get_connection()
    try:
        conn.execute("UPDATE videos SET subtitles_burned = ? WHERE id = ?",
                     (1 if burned else 0, video_id))
        conn.commit()
    finally:
        conn.close()


# Columns update_video_metadata() may touch — same allowlist pattern as
# storage/stories.update_status(), so a dynamic UPDATE can never be built
# from arbitrary caller-controlled column names.
_VIDEO_METADATA_FIELDS = {
    "youtube_title", "youtube_description", "tiktok_caption",
    "tiktok_hashtags", "thumbnail_path", "review_note",
}


def update_video_metadata(video_id: int, **fields) -> None:
    """Update metadata fields on a video (review-gate edits, thumbnail path).

    Raises:
        ValueError: nếu `fields` chứa cột ngoài allowlist `_VIDEO_METADATA_FIELDS`.
    """
    unknown = set(fields) - _VIDEO_METADATA_FIELDS
    if unknown:
        raise ValueError(f"update_video_metadata: unknown field(s) {sorted(unknown)}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields]
    params = list(fields.values()) + [video_id]
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE videos SET {', '.join(set_clauses)} WHERE id = ?", params
        )
        conn.commit()
    finally:
        conn.close()


def get_videos_by_story(story_id: int) -> list[dict]:
    """Videos đã tạo từ 1 story (resume guard: không render lại story đã có video).

    Requires migration 006 (`videos.story_id`); trả [] trên DB pre-migration.
    """
    conn = get_connection()
    try:
        try:
            rows = conn.execute(
                "SELECT * FROM videos WHERE story_id = ? ORDER BY id", (story_id,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_video_experiment(video_id: int, experiment_id: str, arm: str) -> None:
    """Tag a video vào 1 nhánh thí nghiệm A/B (Phase 6 — experiment helper).

    Requires migration 007 (`videos.experiment_id/experiment_arm`). Trên DB
    pre-migration chỉ log warning chứ không raise (giữ pipeline chạy).
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE videos SET experiment_id = ?, experiment_arm = ? WHERE id = ?",
            (experiment_id, arm, video_id),
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        logger.warning("set_video_experiment skipped (need migration 007?): %s", e)
    finally:
        conn.close()


def get_videos_by_experiment(experiment_id: str) -> list[dict]:
    """Mọi video gắn `experiment_id` (Phase 6). [] trên DB pre-migration."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM videos WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_video(video_id: int) -> Optional[dict]:
    """Get a video by ID."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_videos_by_status(status: str) -> list[dict]:
    """Get all videos with a given status."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM videos WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_approved_videos_for_date(date_str: str) -> list[dict]:
    """Get approved videos scheduled for a specific date."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM videos WHERE status = 'approved' AND scheduled_date = ? "
            "ORDER BY created_at", (date_str,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database initialized at:", config.DB_PATH)
