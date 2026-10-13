"""One-time copy of every row from the old SQLite file into PostgreSQL.

Usage (inside the backend container, see install.sh --sqlite):
    DATABASE_URL=postgresql://... python scripts/sqlite_to_postgres.py /path/to/pdos.db

- Target tables are created from models.py (same tables/columns as SQLite).
- Refuses to run if any target table already has rows (no silent duplicates/merges).
- Exits non-zero if any table's row count differs after the copy.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, func, inspect, select, text

from database import Base, engine as pg_engine
import models  # noqa: F401  (registers tables on Base.metadata)


def main(sqlite_path: str) -> int:
    if not os.path.isfile(sqlite_path):
        print(f"SQLite file not found: {sqlite_path}")
        return 1
    if pg_engine.dialect.name != "postgresql":
        print("DATABASE_URL must point to PostgreSQL")
        return 1

    src = create_engine(f"sqlite:///{sqlite_path}")
    src_tables = set(inspect(src).get_table_names())
    Base.metadata.create_all(bind=pg_engine)

    tables = [t for t in Base.metadata.sorted_tables if t.name in src_tables]
    skipped = sorted(src_tables - set(Base.metadata.tables) - {"alembic_version"})
    if skipped:
        print(f"WARNING: SQLite tables not in models.py are NOT copied: {skipped}")

    with pg_engine.begin() as dst:
        non_empty = [t.name for t in tables if dst.execute(select(func.count()).select_from(t)).scalar()]
        if non_empty:
            print(f"Refusing: target tables already contain rows: {non_empty}")
            return 1

        # SQLite never enforced foreign keys, so the old data contains orphans.
        # Replica mode skips FK triggers for this transaction only, so rows are copied exactly as they were.
        dst.execute(text("SET LOCAL session_replication_role = replica"))
        with src.connect() as s:
            for table in tables:
                src_cols = {c["name"] for c in inspect(src).get_columns(table.name)}
                cols = [c for c in table.columns if c.name in src_cols]
                dropped = src_cols - {c.name for c in cols}
                if dropped:
                    print(f"WARNING: {table.name}: columns not in models.py are NOT copied: {sorted(dropped)}")
                rows = [dict(r._mapping) for r in s.execute(select(*cols))]
                if rows:
                    dst.execute(table.insert(), rows)
                print(f"{table.name}: copied {len(rows)}")

    mismatches = []
    with src.connect() as s, pg_engine.connect() as d:
        for table in tables:
            a = s.execute(select(func.count()).select_from(table)).scalar()
            b = d.execute(select(func.count()).select_from(table)).scalar()
            print(f"{table.name:30} sqlite={a:<8} postgres={b:<8} {'OK' if a == b else 'MISMATCH'}")
            if a != b:
                mismatches.append(table.name)
    if mismatches:
        print(f"FAILED: row counts differ for {mismatches}")
        return 1
    print("All row counts match.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
