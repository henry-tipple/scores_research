"""Migrate, then upsert cohorts and instances from the fleet generator.

Usage: python db/seed.py [--database-url URL]
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg

from fleet import generate
from migrate import migrate


def seed(url: str) -> dict:
    migrate(url)
    fleet, cohorts = generate.load_config()
    instances = generate.build_instances(fleet, cohorts)
    errors = generate.validate(fleet, cohorts, instances)
    if errors:
        raise SystemExit("fleet config invalid:\n  " + "\n  ".join(errors))
    sql = generate.render_sql(fleet, cohorts, instances)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql)
        counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0]
                  for t in ("apps", "cohorts", "instances")}
        counts["atlas"] = conn.execute("select count(*) from instances where kind = 'atlas'").fetchone()[0]
        # Seeding only upserts, so instances removed from the config stay in the table (their
        # events keep pointing at them). Report them rather than deleting history.
        wanted = [i.id for i in instances]
        counts["stale"] = [r[0] for r in conn.execute(
            "select id from instances where not (id = any(%s)) order by id", (wanted,))]
    return counts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    a = p.parse_args()
    if not a.database_url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2
    c = seed(a.database_url)
    print(f"seeded: {c['apps']} apps, {c['cohorts']} cohorts, {c['instances']} instances "
          f"({c['instances'] - c['atlas']} cohort + {c['atlas']} atlas)")
    if c["stale"]:
        print(f"warning: {len(c['stale'])} instance(s) in the database are no longer in the fleet config: "
              f"{', '.join(c['stale'])}. Their rows are kept; run `make reset` on a dev stack to start clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
