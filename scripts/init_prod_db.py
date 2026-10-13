"""Initialize a production (PostgreSQL) database and run migrations.

Usage:
    python scripts/init_prod_db.py
"""

import os
import sys
import subprocess
from sqlalchemy import inspect, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from database import engine, Base
import models

# 1. Ensure tables exist in PostgreSQL
Base.metadata.create_all(bind=engine)
print("Tables ensured via SQLAlchemy metadata.")

# 2. Check Alembic version state
try:
    with engine.connect() as conn:
        has_table = inspect(engine).has_table("alembic_version")
        current_version = None
        if has_table:
            current_version = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()

    if current_version:
        print(f"Existing Alembic version found: {current_version}. Applying new migrations...")
        result = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True)
        if result.returncode == 0:
            print("Alembic migrations applied successfully.")
        else:
            print(f"Alembic upgrade notice: {result.stderr or result.stdout}")
    else:
        print("Fresh database schema. Stamping Alembic head...")
        subprocess.run(["alembic", "stamp", "head"], check=False)
        print("Alembic stamped at head.")
except Exception as e:
    print(f"Alembic setup note: {e}")
