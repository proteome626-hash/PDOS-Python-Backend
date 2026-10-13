from sqlalchemy import Column, String, Boolean, DateTime, Float, Integer, ForeignKey, Text
from sqlalchemy.orm import relationship
from database import Base
import datetime
import uuid

def generate_uuid():
    return str(uuid.uuid4())

# ─────────────────────────────────────────────────────────────────────────────
# StoredFile — local server file storage (uploads/ directory)
# ─────────────────────────────────────────────────────────────────────────────
class StoredFile(Base):
    __tablename__ = "stored_files"

    id = Column(String, primary_key=True, default=generate_uuid)
    local_path = Column(String, unique=True, nullable=False)

    original_name = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    sha256 = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# ─────────────────────────────────────────────────────────────────────────────
# Brand — top-level business entity; all commercial data is siloed per brand
# ─────────────────────────────────────────────────────────────────────────────
class Brand(Base):
    __tablename__ = "brands"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True, nullable=False)
    logo_url = Column(String, nullable=True)
    logo_file_id = Column(String, ForeignKey("stored_files.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    users = relationship("User", foreign_keys="[User.brand_id]", back_populates="brand")
    centers = relationship("Center", back_populates="brand")
    products = relationship("Product", back_populates="brand")
    assigned_users = relationship("User", secondary="user_brands", back_populates="brands", overlaps="user,user_brands")

# ─────────────────────────────────────────────────────────────────────────────
# Region & UserRegion — Branch & regional scoping
# ─────────────────────────────────────────────────────────────────────────────
class Region(Base):
    __tablename__ = "regions"

    id = Column(String, primary_key=True, default=generate_uuid)
    name_ar = Column(String, nullable=False)
    name_en = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class UserRegion(Base):
    __tablename__ = "user_regions"

    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    region_id = Column(String, ForeignKey("regions.id"), primary_key=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    region = relationship("Region")

class UserBrand(Base):
    __tablename__ = "user_brands"
    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    brand_id = Column(String, ForeignKey("brands.id"), primary_key=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(String, nullable=False) # 'admin' | 'general_manager' | 'supervisor' | 'rep'
    phone = Column(String)
    region = Column(String)
    region_id = Column(String, ForeignKey("regions.id"), nullable=True)
    supervisor_id = Column(String, ForeignKey("users.id"))
    profile_image_url = Column(String)
    avatar_file_id = Column(String, ForeignKey("stored_files.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    has_completed_onboarding = Column(Boolean, default=False)
    must_change_password = Column(Boolean, default=True)
    brand_id = Column(String, ForeignKey("brands.id"), nullable=True)  # Legacy, to be removed if fully migrating
    created_by = Column(String, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    user_brands = relationship("UserBrand", backref="user")
    user_regions = relationship("UserRegion", backref="user")
    region_rel = relationship("Region", foreign_keys=[region_id])
    assigned_regions = relationship("Region", secondary="user_regions", overlaps="user,user_regions,region")

    @property
    def brand_ids(self):
        return [b.brand_id for b in self.user_brands]

    @property
    def region_ids(self):
        if self.role == 'rep':
            return [self.region_id] if self.region_id else []
        assigned = [ur.region_id for ur in self.user_regions]
        if self.region_id and self.region_id not in assigned:
            assigned.append(self.region_id)
        return assigned

    # Live Tracking Fields
    last_lat = Column(Float, nullable=True)
    last_lng = Column(Float, nullable=True)
    last_location_update = Column(DateTime, nullable=True)

    created_centers = relationship("Center", back_populates="creator", foreign_keys="[Center.created_by]")
    brand = relationship("Brand", foreign_keys=[brand_id], back_populates="users")
    brands = relationship("Brand", secondary="user_brands", back_populates="assigned_users", overlaps="user,user_brands")

    # Self-referential relationship for supervisor
    reps = relationship("User", foreign_keys=[supervisor_id])

class Center(Base):
    __tablename__ = "centers"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    region = Column(String)
    region_id = Column(String, ForeignKey("regions.id"), nullable=True)
    latitude = Column(Float)
    longitude = Column(Float)
    address = Column(String)
    assigned_rep_id = Column(String, ForeignKey("users.id"))
    created_by = Column(String, ForeignKey("users.id"))
    brand_id = Column(String, ForeignKey("brands.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    status = Column(String, default="active")
    rejection_reason = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    region_rel = relationship("Region", foreign_keys=[region_id])
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_centers")
    brand = relationship("Brand", back_populates="centers")

class Product(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    description = Column(String)
    barcode = Column(String, unique=True, nullable=True)
    price = Column(Float, nullable=False)
    category = Column(String, nullable=False)
    stock_qty = Column(Integer, default=0)
    min_threshold = Column(Integer, default=0)
    expiry_date = Column(DateTime, nullable=True)
    expiry_alert_days = Column(Integer, default=90)
    image_url = Column(String)
    image_file_id = Column(String, ForeignKey("stored_files.id"), nullable=True)
    brand_id = Column(String, ForeignKey("brands.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    brand = relationship("Brand", back_populates="products")

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    message = Column(String)
    related_id = Column(String)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Note(Base):
    """Persistent note record — distinct from Notification (which is a
    dismissible alert). Deleting a notification never deletes the Note."""
    __tablename__ = "notes"

    id = Column(String, primary_key=True, default=generate_uuid)
    sender_id = Column(String, ForeignKey("users.id"), nullable=False)
    recipient_id = Column(String, ForeignKey("users.id"), nullable=False)
    visit_id = Column(String, ForeignKey("visits.id"), nullable=True)
    content = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    sender = relationship("User", foreign_keys=[sender_id])
    recipient = relationship("User", foreign_keys=[recipient_id])

class SystemCounter(Base):
    __tablename__ = "system_counters"

    key = Column(String, primary_key=True)
    value = Column(Integer, default=1000)

class UserSignature(Base):
    __tablename__ = "user_signatures"

    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    image_url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


    user = relationship("User", backref="signature")

class Visit(Base):
    __tablename__ = "visits"

    id = Column(String, primary_key=True, default=generate_uuid)
    reference_code = Column(String, unique=True, index=True, nullable=True)
    rep_id = Column(String, ForeignKey("users.id"), nullable=False)
    region_id = Column(String, ForeignKey("regions.id"), nullable=True)
    brand_id = Column(String, ForeignKey("brands.id"), nullable=True)
    center_id = Column(String, ForeignKey("centers.id"), nullable=True)
    client_id = Column(String, ForeignKey("clients.id"), nullable=True)
    appointment_id = Column(String, ForeignKey("appointments.id"), nullable=True)
    visit_date = Column(DateTime, nullable=False)
    arrival_time = Column(DateTime)
    completion_time = Column(DateTime)
    status = Column(String, default="planned") # 'planned', 'arrived', 'completed', 'retroactive'
    notes = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    synced = Column(Boolean, default=False)
    signature_url = Column(String, nullable=True)
    signature_file_id = Column(String, ForeignKey("stored_files.id"), nullable=True)
    reviewed_by = Column(String, ForeignKey("users.id"))
    reviewed_at = Column(DateTime)
    review_note = Column(String)
    supervisor_note = Column(String, nullable=True)
    retroactive_reason = Column(String, nullable=True)
    save_location_lat = Column(Float, nullable=True)
    save_location_lng = Column(Float, nullable=True)
    # Doctor-visit flow
    visit_type = Column(String(20), nullable=False, default="center")  # 'center' | 'doctor'
    visit_reason = Column(Text, nullable=True)
    interested_product_ids = Column(Text)  # JSON array of product ids
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


    # Non-persistent field populated by API layer for the rep's role
    rep_role = None

    region_rel = relationship("Region", foreign_keys=[region_id])
    center = relationship("Center", foreign_keys=[center_id])
    client = relationship("Client", foreign_keys=[client_id])
    # Relationship for retrieving items easily
    items = relationship("VisitItem", back_populates="visit")
    photos = relationship("VisitPhoto", back_populates="visit")
    special_requests = relationship("SpecialRequest", back_populates="visit")

class VisitItem(Base):
    __tablename__ = "visit_items"

    id = Column(String, primary_key=True, default=generate_uuid)
    visit_id = Column(String, ForeignKey("visits.id"), nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    qty_sold = Column(Integer, default=0)
    qty_free = Column(Integer, default=0)
    price_at_sale = Column(Float, nullable=False, default=0.0) # For historical accuracy
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    visit = relationship("Visit", back_populates="items")
    product = relationship("Product")

class VisitPhoto(Base):
    __tablename__ = "visit_photos"

    id = Column(String, primary_key=True, default=generate_uuid)
    visit_id = Column(String, ForeignKey("visits.id"), nullable=False)
    photo_url = Column(String, nullable=False)
    photo_file_id = Column(String, ForeignKey("stored_files.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    visit = relationship("Visit", back_populates="photos")

class Target(Base):
    __tablename__ = "targets"

    id = Column(String, primary_key=True, default=generate_uuid)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    rep_id = Column(String, ForeignKey("users.id")) # Null means global target
    brand_id = Column(String, ForeignKey("brands.id"), nullable=True)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    target_qty = Column(Integer, default=0)
    notes = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class TargetView(Base):
    __tablename__ = "target_views"

    id = Column(String, primary_key=True, default=generate_uuid)
    target_id = Column(String, ForeignKey("targets.id"), nullable=False)
    rep_id = Column(String, ForeignKey("users.id"), nullable=False)
    viewed_at = Column(DateTime, default=datetime.datetime.utcnow)

class Expense(Base):
    __tablename__ = "expenses"

    id = Column(String, primary_key=True, default=generate_uuid)
    visit_id = Column(String, ForeignKey("visits.id"), nullable=True)
    rep_id = Column(String, ForeignKey("users.id"), nullable=False)
    category = Column(String, nullable=False)  # transport, meals, supplies, other
    amount = Column(Float, nullable=False)
    description = Column(String)
    receipt_image_url = Column(String)
    receipt_file_id = Column(String, ForeignKey("stored_files.id"), nullable=True)
    status = Column(String, default="pending", nullable=False)
    rejection_reason = Column(String)
    requires_admin_approval = Column(Boolean, default=False)
    requires_gm_approval = Column(Boolean, default=False)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


    # Relationships
    approved_by_user = relationship("User", foreign_keys=[approved_by])

class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(String, primary_key=True, default=generate_uuid)
    reference_code = Column(String, unique=True, index=True, nullable=True)
    rep_id = Column(String, ForeignKey("users.id"), nullable=False)
    client_id = Column(String, ForeignKey("clients.id"), nullable=True)
    center_id = Column(String, ForeignKey("centers.id"), nullable=True)
    appt_date = Column(DateTime, nullable=False)
    appt_time = Column(String, nullable=False)
    reminder_minutes_before = Column(Integer, default=30)
    notes = Column(String)
    supervisor_note = Column(String, nullable=True)
    suggested_product_id = Column(String, ForeignKey("products.id"), nullable=True)
    status = Column(String, default="pending") # 'pending', 'done', 'missed'
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class PharmacyStockCheck(Base):
    __tablename__ = "pharmacy_stock_checks"

    id = Column(String, primary_key=True, default=generate_uuid)
    visit_id = Column(String, ForeignKey("visits.id"), nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=True)
    competitor_product_name = Column(String, nullable=True)
    observed_qty = Column(Integer, nullable=False, default=0)
    synced = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class SpecialRequest(Base):
    __tablename__ = "special_requests"

    id = Column(String, primary_key=True, default=generate_uuid)
    visit_id = Column(String, ForeignKey("visits.id"), nullable=False)
    request_type = Column(String, nullable=False)  # e.g., 'brochure', 'sample', 'gift', 'other'
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    visit = relationship("Visit", back_populates="special_requests")

class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    description = Column(String)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# DEPRECATED / REMOVED by migration 7c0af61b59ff:
# class RoutePoint(Base):
#     __tablename__ = "route_points"
#     id = Column(String, primary_key=True, default=generate_uuid)
#     rep_id = Column(String, ForeignKey("users.id"), nullable=False)
#     latitude = Column(Float, nullable=False)
#     longitude = Column(Float, nullable=False)
#     recorded_at = Column(DateTime, nullable=False)
#     created_at = Column(DateTime, default=datetime.datetime.utcnow)
#
# Reason: violates privacy decision "no GPS breadcrumb trail, only live location."
# The table was dropped by migration 7c0af61b59ff. Do NOT restore without explicit
# documented approval of the live-tracking privacy policy.

class Client(Base):
    __tablename__ = "clients"

    id = Column(String, primary_key=True, default=generate_uuid)
    rep_id = Column(String, ForeignKey("users.id"), nullable=True)
    facility_name = Column(String)
    facility_type = Column(String)
    doctor_name = Column(String)
    specialty = Column(String)
    birth_date = Column(DateTime, nullable=True)
    class_tier = Column(String)
    relationship_type = Column(String)
    description = Column(String)
    phone_number = Column(String)
    region = Column(String)
    region_id = Column(String, ForeignKey("regions.id"), nullable=True)
    area = Column(String)
    street = Column(String)
    nearby_landmark = Column(String)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    photo_url = Column(String)
    photo_file_id = Column(String, ForeignKey("stored_files.id"), nullable=True)
    # Doctor profiling (doctor-visit flow)
    gender = Column(String(10))            # 'male' | 'female'
    rating = Column(Integer)               # 1..5 stars
    treatment_quality = Column(String(20)) # 'good' | 'average' | 'bad'
    
    # New fields
    brand_id = Column(String, ForeignKey('brands.id'), nullable=True)
    client_type = Column(String, default="doctor")
    status = Column(String, default="active")
    scientific_interests = Column(String)
    product_interests = Column(String)
    pharmacy_type = Column(String)
    institution_type = Column(String)
    key_contact_name = Column(String)
    key_contact_position = Column(String)
    key_contact_phone = Column(String)
    departments = Column(String)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    region_rel = relationship("Region", foreign_keys=[region_id])


class FCMToken(Base):
    __tablename__ = "fcm_tokens"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class FieldReport(Base):
    __tablename__ = "field_reports"

    id = Column(String, primary_key=True, default=generate_uuid)
    rep_id = Column(String, ForeignKey("users.id"), nullable=False)
    content = Column(String, nullable=False)
    photo_url = Column(String, nullable=True)
    photo_file_id = Column(String, ForeignKey("stored_files.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    user_name = Column(String, nullable=False)
    action = Column(String, nullable=False)
    log_type = Column(String, nullable=False)  # visit, order, alert, user, config
    related_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=generate_uuid)
    rep_id = Column(String, ForeignKey("users.id"), nullable=False)
    region_id = Column(String, ForeignKey("regions.id"), nullable=True)
    brand_id = Column(String, ForeignKey("brands.id"), nullable=False)
    supervisor_id = Column(String, ForeignKey("users.id"), nullable=True)
    task_type = Column(String(30), nullable=False)
    target_type = Column(String(20), nullable=True)
    target_id = Column(String, nullable=True)
    target_name = Column(String, nullable=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=True)
    product_name = Column(String, nullable=True)
    quantity_target = Column(Integer, nullable=True)
    due_date = Column(DateTime, nullable=True)
    status = Column(String(20), default='pending')
    priority = Column(String(10), default='normal')
    notes = Column(Text, nullable=True)
    progress_note = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    purpose = Column(String(30), nullable=True)
    visit_subtype = Column(String(20), nullable=True)
    scheduled_datetime = Column(DateTime, nullable=True)
    rejection_report = Column(Text, nullable=True)
    reminder_offset = Column(String(10), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    visit_id = Column(String, ForeignKey("visits.id"), nullable=True)
    visit_started_at = Column(DateTime, nullable=True)
    visit_completed_at = Column(DateTime, nullable=True)

    region_rel = relationship("Region", foreign_keys=[region_id])
    rep = relationship("User", foreign_keys=[rep_id])
    brand = relationship("Brand", foreign_keys=[brand_id])
    supervisor = relationship("User", foreign_keys=[supervisor_id])

class BrandActivityLog(Base):
    __tablename__ = "brand_activity_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    rep_id = Column(String, ForeignKey("users.id"), nullable=True)
    brand_id = Column(String, ForeignKey("brands.id"), nullable=False)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=True)
    activity_type = Column(String(30), nullable=True)
    target_type = Column(String(20), nullable=True)
    target_id = Column(String, nullable=True)
    target_name = Column(String, nullable=True)
    logged_at = Column(DateTime, default=datetime.datetime.utcnow)
    notes = Column(Text, nullable=True)
    status = Column(String(20), default='completed')

    rep = relationship("User", foreign_keys=[rep_id])
    brand = relationship("Brand", foreign_keys=[brand_id])

class TaskHistory(Base):
    __tablename__ = "task_history"

    id = Column(String, primary_key=True, default=generate_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    action = Column(String(20), nullable=False)  # created, status_changed, updated, deleted
    old_status = Column(String(20), nullable=True)
    new_status = Column(String(20), nullable=True)
    changed_by = Column(String, ForeignKey("users.id"), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    task = relationship("Task", foreign_keys=[task_id])
    user = relationship("User", foreign_keys=[changed_by])
