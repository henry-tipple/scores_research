"""Apply db/schema.sql (if needed) and then db/migrations/NNN_*.sql in order.

Usage: python db/migrate.py [--database-url URL] [--reset]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

import psycopg

DB_DIR = pathlib.Path(__file__).resolve().parent


def migrate(url: str, reset: bool = False) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        if reset:
            conn.execute("drop schema public cascade; create schema public;")
        has_table = conn.execute(
            "select to_regclass('public.schema_migrations') is not null"
        ).fetchone()[0]
        if not has_table:
            conn.execute((DB_DIR / "schema.sql").read_text())
            applied.append("000_baseline")
        done = {r[0] for r in conn.execute("select version from schema_migrations")}
        for path in sorted((DB_DIR / "migrations").glob("[0-9][0-9][0-9]_*.sql")):
            version = path.stem
            if version in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("insert into schema_migrations (version) values (%s)", (version,))
            applied.append(version)
    return applied


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    p.add_argument("--reset", action="store_true", help="drop everything first (dev only)")
    a = p.parse_args()
    if not a.database_url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2
    applied = migrate(a.database_url, a.reset)
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
