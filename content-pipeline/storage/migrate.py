from __future__ import annotations

"""
Migration runner — áp dụng các file SQL trong storage/migrations/ theo thứ tự,
lưu version đã apply vào bảng `_migrations` để idempotent.

Usage:
    python -m storage.migrate up        # áp dụng mọi migration còn pending
    python -m storage.migrate status    # in danh sách migration đã/chưa apply
    python -m storage.migrate down      # rollback migration mới nhất

Convention: mỗi migration là 1 file `<version>.sql` (vd `001_multi_track.sql`).
File rollback tương ứng (tuỳ chọn) là `<version>_down.sql` và bị bỏ qua khi
liệt kê migration "up".
"""

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "version TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    return conn


def _discover_migrations() -> list[tuple[str, Path]]:
    """Return (version, up_sql_path) sorted by version, skipping *_down.sql files."""
    result = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.stem.endswith("_down"):
            continue
        result.append((path.stem, path))
    return result


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT version FROM _migrations").fetchall()
    return {r[0] for r in rows}


def migrate_up(conn: sqlite3.Connection | None = None) -> list[str]:
    """Apply all pending migrations in order. Returns the list of versions applied."""
    owns_conn = conn is None
    conn = conn or _connection()
    try:
        applied = _applied_versions(conn)
        newly_applied = []
        for version, path in _discover_migrations():
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute("INSERT INTO _migrations (version) VALUES (?)", (version,))
            conn.commit()
            newly_applied.append(version)
            logger.info("Applied migration: %s", version)
        return newly_applied
    finally:
        if owns_conn:
            conn.close()


def migrate_down(conn: sqlite3.Connection | None = None) -> str | None:
    """Revert the most recently applied migration. Returns its version, or None if none applied."""
    owns_conn = conn is None
    conn = conn or _connection()
    try:
        row = conn.execute(
            "SELECT version FROM _migrations ORDER BY applied_at DESC, version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            logger.info("No migrations to revert.")
            return None
        version = row[0]
        down_path = MIGRATIONS_DIR / f"{version}_down.sql"
        if not down_path.exists():
            raise FileNotFoundError(f"No down migration found for {version}: {down_path}")
        sql = down_path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute("DELETE FROM _migrations WHERE version = ?", (version,))
        conn.commit()
        logger.info("Reverted migration: %s", version)
        return version
    finally:
        if owns_conn:
            conn.close()


def status(conn: sqlite3.Connection | None = None) -> list[dict]:
    """Return [{"version": ..., "applied": bool}, ...] for every discovered migration."""
    owns_conn = conn is None
    conn = conn or _connection()
    try:
        applied = _applied_versions(conn)
        return [
            {"version": version, "applied": version in applied}
            for version, _ in _discover_migrations()
        ]
    finally:
        if owns_conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="SQLite migration runner")
    parser.add_argument("command", choices=["up", "down", "status"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "up":
        applied = migrate_up()
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}" if applied
              else "No pending migrations.")
    elif args.command == "down":
        reverted = migrate_down()
        print(f"Reverted: {reverted}" if reverted else "Nothing to revert.")
    elif args.command == "status":
        for entry in status():
            mark = "[x]" if entry["applied"] else "[ ]"
            print(f"{mark} {entry['version']}")


if __name__ == "__main__":
    main()
