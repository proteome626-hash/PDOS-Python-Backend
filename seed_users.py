import os
import uuid
from database import SessionLocal, engine
import models
from auth import get_password_hash

models.Base.metadata.create_all(bind=engine)
db = SessionLocal()

users_to_seed = [
    {"email": "admin@pdos.com", "role": "admin", "full_name": "System Admin"},
    {"email": "overseer@pdos.com", "role": "overseer", "full_name": "Global Overseer"},
    {"email": "supervisor@pdos.com", "role": "supervisor", "full_name": "Regional Supervisor"},
    {"email": "rep@pdos.com", "role": "rep", "full_name": "Sales Rep"},
    {"email": "gm@pdos.com", "role": "general_manager", "full_name": "General Manager"},
]

password = "password123"
hashed = get_password_hash(password)

for u in users_to_seed:
    existing = db.query(models.User).filter(models.User.email == u["email"]).first()
    if not existing:
        new_user = models.User(
            id=str(uuid.uuid4()),
            email=u["email"],
            hashed_password=hashed,
            full_name=u["full_name"],
            role=u["role"],
            has_completed_onboarding=True,
            must_change_password=False,
            is_active=True
        )
        db.add(new_user)

db.commit()
db.close()
print("Users seeded successfully.")
