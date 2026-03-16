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
        # Indexes for frequently queried columns
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_ai_score ON articles(ai_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_created_at ON articles(created_at)")
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
    """Get top-scored articles that haven't been analyzed yet."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE ai_score >= ? AND ai_analysis IS NULL AND status = 'pending' "
            "ORDER BY ai_score DESC LIMIT ?",
            (threshold, limit),
        ).fetchall()
        return [dict(r) for r in rows]
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database initialized at:", config.DB_PATH)
