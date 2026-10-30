"""Verify an isolated ContentCreator DB, without Telegram/OAuth or publishing jobs."""

import argparse
import json
from pathlib import Path
import sqlite3


def verify(database):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError(
            "Create a fresh Dev DB with application migrations first; never reuse the live DB"
        )
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Dev database integrity failed")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"videos", "stories", "scheduled_posts"} <= tables:
            raise ValueError("Application schema/migrations are incomplete")
    return {
        "database": True,
        "publishing_started": False,
        "services": False,
        "user_path": False,
        "publishing_guard": False,
        "ready_for_uat": False,
        "next": "Ops must register a separate Dev runtime with no production credentials, disable all publishing and verify a preview/user path before acceptance.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    print(json.dumps(verify(parser.parse_args().database), indent=2))
