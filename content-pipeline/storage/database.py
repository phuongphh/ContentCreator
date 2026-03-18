from __future__ import annotations

import sqlite3
import json
import logging
from datetime import datetime
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Indexes for frequently queried columns
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_ai_score ON articles(ai_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_created_at ON articles(created_at)")
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
                   summary: Optional[str] = None) -> Optional[int]:
    """Insert a new article. Returns the article id or None if duplicate."""
    if article_exists(url):
        logger.debug("Article already exists: %s", url)
        return None
    conn = get_connection()
    try:
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


def get_articles_for_analysis(threshold: float, limit: int = 5) -> list[dict]:
    """Get top-scored articles that haven't been analyzed yet.

    Takes top N by score. If fewer than `limit` articles pass `threshold`,
    backfills with the next highest-scored articles to ensure enough content.
    """
    conn = get_connection()
    try:
        # First: articles above threshold
        rows = conn.execute(
            "SELECT * FROM articles WHERE ai_score >= ? AND ai_analysis IS NULL AND status = 'pending' "
            "ORDER BY ai_score DESC LIMIT ?",
            (threshold, limit),
        ).fetchall()
        result = [dict(r) for r in rows]

        # Backfill: if not enough, grab next best regardless of threshold
        if len(result) < limit:
            existing_ids = {r["id"] for r in result}
            remaining = limit - len(result)
            backfill_rows = conn.execute(
                "SELECT * FROM articles WHERE ai_score IS NOT NULL AND ai_analysis IS NULL "
                "AND status = 'pending' ORDER BY ai_score DESC LIMIT ?",
                (limit + len(existing_ids),),
            ).fetchall()
            for r in backfill_rows:
                if len(result) >= limit:
                    break
                row = dict(r)
                if row["id"] not in existing_ids:
                    result.append(row)

        return result
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


def get_report_articles(score_threshold_notify: float) -> dict:
    """Get articles grouped by urgency for the daily report."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE ai_score >= ? AND ai_analysis IS NOT NULL AND status = 'pending' "
            "ORDER BY ai_score DESC",
            (score_threshold_notify,),
        ).fetchall()
        result = {"immediate": [], "this_week": [], "backlog": []}
        for r in rows:
            article = dict(r)
            urgency = article.get("urgency", "backlog")
            if urgency in result:
                result[urgency].append(article)
            else:
                result["backlog"].append(article)
        return result
    finally:
        conn.close()


def get_top_analyzed_articles(limit: int = 5) -> list[dict]:
    """Get top N analyzed articles by score, regardless of threshold.

    Used for the daily narrative report — always returns up to `limit` articles.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE ai_analysis IS NOT NULL AND status = 'pending' "
            "ORDER BY ai_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
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
                 scheduled_platform: str = "") -> int:
    """Insert a new video record. Returns the video id."""
    conn = get_connection()
    try:
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
