"""Normalize legacy free-text regions and assign supervisor regions.

Revision ID: c2d3e4f5a6b7
Revises: b1a2c3d4e5f6
"""
from typing import Sequence, Union
import unicodedata
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ALIASES = {
    "erbil": "erbil", "arbil": "erbil", "اربيل": "erbil", "أربيل": "erbil",
    "duhok": "duhok", "dohuk": "duhok", "دهوك": "duhok",
    "baghdad": "baghdad", "بغداد": "baghdad",
    "kirkuk": "kirkuk", "كركوك": "kirkuk",
    "sulaymaniyah": "sulaymaniyah", "sulaimaniya": "sulaymaniyah",
    "السليمانية": "sulaymaniyah", "سليمانية": "sulaymaniyah",
    "basra": "basra", "البصرة": "basra",
    "nineveh": "nineveh", "mosul": "nineveh", "نينوى": "nineveh", "الموصل": "nineveh",
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    return "".join(ch for ch in value if not ch.isspace() and ch not in "-_")


def upgrade() -> None:
    conn = op.get_bind()
    regions = conn.execute(sa.text("SELECT id, name_ar, name_en FROM regions")).mappings().all()
    canonical_ids = {}
    for region in regions:
        for name in (region["name_ar"], region["name_en"]):
            if name:
                canonical_ids[_normalize(name)] = region["id"]
    for alias, canonical in ALIASES.items():
        match = next(
            (
                r["id"] for r in regions
                if _normalize(r["name_en"]) == canonical
                or _normalize(r["name_ar"]) == canonical
            ),
            None,
        )
        if match:
            canonical_ids[_normalize(alias)] = match

    for table in ("users", "clients", "centers"):
        entries = conn.execute(
            sa.text(f"SELECT DISTINCT region FROM {table} WHERE region IS NOT NULL AND TRIM(region) != ''")
        ).scalars().all()
        for value in entries:
            key = _normalize(value)
            region_id = canonical_ids.get(key)
            if not region_id:
                region_id = str(uuid.uuid4())
                conn.execute(
                    sa.text(
                        "INSERT INTO regions (id, name_ar, name_en, is_active) "
                        "VALUES (:id, :ar, :en, 1)"
                    ),
                    {"id": region_id, "ar": value.strip(), "en": value.strip()},
                )
                canonical_ids[key] = region_id
            conn.execute(
                sa.text(f"UPDATE {table} SET region_id = :rid WHERE region_id IS NULL AND region = :region"),
                {"rid": region_id, "region": value},
            )

    conn.execute(sa.text("""
        UPDATE visits
        SET region_id = COALESCE(
            (SELECT region_id FROM users WHERE users.id = visits.rep_id),
            (SELECT region_id FROM clients WHERE clients.id = visits.client_id),
            (SELECT region_id FROM centers WHERE centers.id = visits.center_id)
        )
        WHERE region_id IS NULL
    """))
    conn.execute(sa.text("""
        UPDATE tasks
        SET region_id = (SELECT region_id FROM users WHERE users.id = tasks.rep_id)
        WHERE region_id IS NULL
    """))

    supervisors = conn.execute(sa.text("""
        SELECT DISTINCT supervisor_id, region_id
        FROM users
        WHERE role = 'rep' AND supervisor_id IS NOT NULL AND region_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM user_regions
              WHERE user_regions.user_id = users.supervisor_id
          )
    """)).all()
    for supervisor_id, region_id in supervisors:
        exists = conn.execute(
            sa.text("SELECT 1 FROM user_regions WHERE user_id = :uid AND region_id = :rid"),
            {"uid": supervisor_id, "rid": region_id},
        ).first()
        if not exists:
            conn.execute(
                sa.text("INSERT INTO user_regions (user_id, region_id) VALUES (:uid, :rid)"),
                {"uid": supervisor_id, "rid": region_id},
            )


def downgrade() -> None:
    pass
