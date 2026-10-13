import uuid
import datetime
from sqlalchemy.orm import Session
from database import SessionLocal, engine, Base
from models import User, Center, Product, Appointment, Target, Brand
from auth import get_password_hash
import random

def seed_db():
    db = SessionLocal()

    # 0. Ensure a default Brand exists
    brand = db.query(Brand).filter(Brand.name == "Default").first()
    if not brand:
        brand = Brand(id=str(uuid.uuid4()), name="Default")
        db.add(brand)
        db.commit()
        db.refresh(brand)
        print("Created default brand.")
    else:
        print("Default brand already exists.")
    
    # 1. Create a Rep User if it doesn't exist
    rep = db.query(User).filter(User.email == "rep@pdos.com").first()
    if not rep:
        rep = User(
            email="rep@pdos.com",
            hashed_password=get_password_hash("password123"),
            full_name="Ahmed Field Rep",
            role="rep",
            brand_id=brand.id,
            phone="0500000000",
            region="Riyadh",
            is_active=True,
            must_change_password=False
        )
        db.add(rep)
        db.commit()
        db.refresh(rep)
        print("Created dummy Rep user: rep@pdos.com (password123)")
    else:
        print("Rep user already exists.")

    # 2. Add Dummy Products
    if db.query(Product).count() == 0:
        products = [
            Product(name="Panadol Extra", description="Pain reliever", price=12.5, category="Tablets", stock_qty=500, min_threshold=50, brand_id=brand.id),
            Product(name="Amoxil 500mg", description="Antibiotic", price=25.0, category="Capsules", stock_qty=200, min_threshold=20, brand_id=brand.id),
            Product(name="Cough Syrup", description="For dry cough", price=18.0, category="Syrups", stock_qty=150, min_threshold=30, brand_id=brand.id),
            Product(name="Vitamin C 1000mg", description="Immunity booster", price=30.0, category="Vitamins", stock_qty=300, min_threshold=50, brand_id=brand.id),
            Product(name="Omega 3 Fish Oil", description="Heart health", price=45.0, category="Vitamins", stock_qty=100, min_threshold=10, brand_id=brand.id)
        ]
        db.add_all(products)
        db.commit()
        print("Added 5 dummy products.")
    else:
        print("Products already exist.")

    # 3. Add Dummy Centers
    if db.query(Center).count() == 0:
        centers = [
            Center(name="Al Nahdi Pharmacy - Olaya", region="Riyadh", latitude=24.7136, longitude=46.6753, address="Olaya St", assigned_rep_id=rep.id, brand_id=brand.id),
            Center(name="Boots Pharmacy - Riyadh Park", region="Riyadh", latitude=24.7555, longitude=46.6231, address="Riyadh Park Mall", assigned_rep_id=rep.id, brand_id=brand.id),
            Center(name="Aldawaa Pharmacy - Malaz", region="Riyadh", latitude=24.6710, longitude=46.7329, address="Malaz District", assigned_rep_id=rep.id, brand_id=brand.id)
        ]
        db.add_all(centers)
        db.commit()
        print("Added 3 dummy centers.")
    else:
        print("Centers already exist.")

    # 4. Add Dummy Appointments for Today
    centers_in_db = db.query(Center).all()
    if db.query(Appointment).count() == 0 and len(centers_in_db) >= 2:
        today = datetime.date.today()
        appts = [
            Appointment(rep_id=rep.id, center_id=centers_in_db[0].id, appt_date=today, appt_time="10:00", status="pending", notes="Monthly checkup"),
            Appointment(rep_id=rep.id, center_id=centers_in_db[1].id, appt_date=today, appt_time="14:30", status="pending", notes="Introduce new Vitamin C")
        ]
        db.add_all(appts)
        db.commit()
        print("Added 2 dummy appointments for today.")
    else:
        print("Appointments already exist.")

    # 5. Add a Target for the Rep
    products_in_db = db.query(Product).all()
    if db.query(Target).count() == 0 and len(products_in_db) > 0:
        target = Target(
            rep_id=rep.id,
            product_id=products_in_db[0].id,
            target_qty=1000,
            period_start=datetime.date.today().replace(day=1),
            period_end=datetime.date.today().replace(day=28)
        )
        db.add(target)
        db.commit()
        print("Added 1 dummy target for the rep.")
    else:
        print("Targets already exist.")

    db.close()
    print("Database seeding completed.")

if __name__ == "__main__":
    seed_db()
