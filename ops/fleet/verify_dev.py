"""Verify Dev schema against real candidate migrations without touching live data."""

import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

APPLICATION = Path(__file__).resolve().parents[2] / "content-pipeline"
REFERENCE = """
import sys, types
sys.path.insert(0,sys.argv[1])
# Load only DB/migration code, with a disposable DB and no dotenv/provider config.
sys.modules['config']=types.SimpleNamespace(DB_PATH=sys.argv[2])
from storage.migrate import migrate_up
migrate_up()
"""


@contextmanager
def reference_database():
    with tempfile.TemporaryDirectory(prefix="fleet-content-schema-") as temporary:
        path = Path(temporary) / "reference.sqlite3"
        subprocess.run(
            [sys.executable, "-I", "-c", REFERENCE, str(APPLICATION), str(path)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        yield path


def schema(connection):
    # sqlite_master names come from the candidate's migration code; quote identifiers.
    tables = {}
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        quoted = '"' + name.replace('"', '""') + '"'
        tables[name] = {
            row[1]: tuple(row[2:])
            for row in connection.execute("PRAGMA table_info(" + quoted + ")")
        }
    indexes = {
        name: " ".join(sql.split())
        for name, sql in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    }
    return tables, indexes


def verify(database):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError(
            "Initialize a separate Dev DB with application migrations first"
        )
    with (
        reference_database() as reference,
        closing(sqlite3.connect(reference)) as expected,
    ):
        required_tables, required_indexes = schema(expected)
        required_versions = {
            row[0] for row in expected.execute("SELECT version FROM _migrations")
        }
    with closing(
        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Dev database integrity failed")
        tables, indexes = schema(connection)
        for table, columns in required_tables.items():
            if table not in tables or any(
                tables[table].get(name) != info for name, info in columns.items()
            ):
                raise ValueError("Application schema/migrations incomplete: " + table)
        if any(indexes.get(name) != sql for name, sql in required_indexes.items()):
            raise ValueError("Required application indexes are missing or changed")
        applied = {
            row[0] for row in connection.execute("SELECT version FROM _migrations")
        }
        if applied != required_versions:
            raise ValueError("Migration versions do not match the candidate source")
    return {
        "database": True,
        "migrations_verified": len(required_versions),
        "publishing_started": False,
        "services": False,
        "user_path": False,
        "publishing_guard": False,
        "ready_for_uat": False,
        "next": "Ops must register isolated runtime/credentials, disable publishing and verify the preview/user path before acceptance.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    print(json.dumps(verify(parser.parse_args().database), indent=2))
