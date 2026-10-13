"""Initialize a production (PostgreSQL) database.

Usage:
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/pdos python scripts/init_prod_db.py
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from database import engine, Base
import models

Base.metadata.create_all(bind=engine)
print("Tables created via SQLAlchemy metadata.")

os.system("alembic stamp head")
print("Alembic stamped at head.")
