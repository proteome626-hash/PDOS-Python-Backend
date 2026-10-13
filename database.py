import os
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Use DATABASE_URL from environment if available, otherwise default to local SQLite
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./pdos.db")
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
pool_kwargs = {}
if not is_sqlite:
    pool_kwargs = {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", "20")),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        "pool_pre_ping": True,
    }
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args=connect_args, **pool_kwargs
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def ensure_schema_and_regions(db_engine=engine):
    """Ensure missing columns and default regions exist, whether running locally or in tests."""
    from sqlalchemy import inspect, text
    import uuid
    try:
        inspector = inspect(db_engine)
        tables = inspector.get_table_names()

        for table_name in ["users", "centers", "clients", "visits", "tasks"]:
            if table_name in tables:
                cols = [c["name"] for c in inspector.get_columns(table_name)]
                if "region_id" not in cols:
                    with db_engine.connect() as conn:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN region_id VARCHAR"))
                        conn.commit()

        if "regions" in tables:
            with db_engine.connect() as conn:
                cnt = conn.execute(text("SELECT COUNT(*) FROM regions")).scalar()
                if cnt == 0:
                    default_regions = [
                        ("أربيل", "Erbil"),
                        ("دهوك", "Duhok"),
                        ("بغداد", "Baghdad"),
                        ("كركوك", "Kirkuk"),
                        ("السليمانية", "Sulaymaniyah"),
                        ("البصرة", "Basra"),
                        ("نينوى", "Nineveh"),
                    ]
                    for ar, en in default_regions:
                        conn.execute(
                            text("INSERT INTO regions (id, name_ar, name_en, is_active) VALUES (:id, :ar, :en, :active)"),
                            {"id": str(uuid.uuid4()), "ar": ar, "en": en, "active": True}
                        )
                    conn.commit()
    except Exception as e:
        # Don't block if in-memory or transient issue
        pass

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

