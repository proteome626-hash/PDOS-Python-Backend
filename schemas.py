import json
import math
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Literal, Optional, List
from datetime import datetime

class RegionBase(BaseModel):
    name_ar: str
    name_en: str
    is_active: bool = True

class RegionCreate(RegionBase):
    pass

class RegionUpdate(BaseModel):
    name_ar: Optional[str] = None
    name_en: Optional[str] = None
    is_active: Optional[bool] = None

class RegionResponse(RegionBase):
    id: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class UserSignatureResponse(BaseModel):
    user_id: str
    image_url: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    role: Literal["admin", "general_manager", "supervisor", "rep", "overseer"]
    phone: Optional[str] = None
    region: Optional[str] = None
    region_id: Optional[str] = None
    region_ids: List[str] = Field(default_factory=list)
    supervisor_id: Optional[str] = None
    profile_image_url: Optional[str] = None
    is_active: bool = True
    brand_id: Optional[str] = None
    brand_ids: List[str] = Field(default_factory=list)

class UserCreate(UserBase):
    temporary_password: str = Field(min_length=8)

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    region: Optional[str] = None
    region_id: Optional[str] = None
    region_ids: Optional[List[str]] = None
    supervisor_id: Optional[str] = None
    profile_image_url: Optional[str] = None
    has_completed_onboarding: Optional[bool] = None
    is_active: Optional[bool] = None
    brand_ids: Optional[List[str]] = None

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    region: Optional[str] = None
    region_id: Optional[str] = None
    profile_image_url: Optional[str] = None
    has_completed_onboarding: Optional[bool] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None

class PublicProfileResponse(BaseModel):
    id: str
    full_name: str
    profile_image_url: Optional[str] = None
    role: str
    brand_id: Optional[str] = None
    brand_ids: List[str] = []
    brand_name: Optional[str] = None
    supervisor_id: Optional[str] = None
    completed_visits: int = 0
    target_achievement_pct: float = 0.0
    team_count: int = 0

class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    barcode: Optional[str] = None
    price: float
    category: str
    stock_qty: int = 0
    min_threshold: int = 0
    image_url: Optional[str] = None
    brand_id: Optional[str] = None

class UserResponse(UserBase):
    id: str
    has_completed_onboarding: bool
    must_change_password: bool
    avatar_file_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_lat: Optional[float] = None
    last_lng: Optional[float] = None
    last_location_update: Optional[datetime] = None
    brand_id: Optional[str] = None
    brand_ids: List[str] = []

    @field_validator("email", mode="before")
    @classmethod
    def normalize_legacy_erased_email(cls, value):
        if isinstance(value, str) and value.startswith("deleted-") and value.endswith(
            "@invalid.local"
        ):
            return f"{value.removesuffix('@invalid.local')}@redacted-account.net"
        return value

    class Config:
        from_attributes = True

class TeamDirectoryResponse(BaseModel):
    supervisor: UserResponse
    reps: List[UserResponse]

class LocationUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)

    @field_validator("lat", "lng", mode="before")
    @classmethod
    def reject_non_finite_coordinates(cls, value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class TokenData(BaseModel):
    id: Optional[str] = None

class CenterBase(BaseModel):
    name: str
    region: Optional[str] = None
    region_id: Optional[str] = None
    brand_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    assigned_rep_id: Optional[str] = None

class CenterCreate(CenterBase):
    pass

class CenterUpdate(BaseModel):
    name: Optional[str] = None
    region: Optional[str] = None
    region_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    assigned_rep_id: Optional[str] = None
    is_active: Optional[bool] = None

class CenterResponse(CenterBase):
    id: str
    brand_id: Optional[str] = None
    brand_ids: List[str] = []
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class CenterCoverageResponse(BaseModel):
    center_id: str
    name: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    last_visit_date: Optional[datetime] = None
    assigned_rep_id: Optional[str] = None
    status: str
    has_supervisor_note: bool = False
    supervisor_note: Optional[str] = None

class ProductBase(BaseModel):
    name: str
    description: Optional[str] = None
    barcode: Optional[str] = None
    price: float
    category: str
    stock_qty: int = 0
    min_threshold: int = 0
    image_url: Optional[str] = None

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    description: Optional[str] = None
    barcode: Optional[str] = None
    stock_qty: Optional[int] = None
    min_threshold: Optional[int] = None
    image_url: Optional[str] = None

class ProductResponse(ProductBase):
    id: str
    brand_id: Optional[str] = None
    brand_ids: List[str] = []
    is_active: bool
    barcode: Optional[str] = None
    image_file_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# ─────────────────────────────────────────────────────────────────────────────
# Brand schemas
# ─────────────────────────────────────────────────────────────────────────────
class BrandCreate(BaseModel):
    name: str
    logo_url: Optional[str] = None

class BrandUpdate(BaseModel):
    name: Optional[str] = None
    logo_url: Optional[str] = None
    is_active: Optional[bool] = None

class BrandResponse(BaseModel):
    id: str
    name: str
    logo_url: Optional[str] = None
    logo_file_id: Optional[str] = None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class NotificationResponse(BaseModel):
    id: str
    user_id: str
    type: str
    title: str
    message: Optional[str] = None
    related_id: Optional[str] = None
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True

class VisitItemBase(BaseModel):
    product_id: str
    qty_sold: int = 0
    qty_free: int = 0

class VisitItemCreate(VisitItemBase):
    price_at_sale: Optional[float] = None

class VisitItemResponse(VisitItemBase):
    id: str
    visit_id: str
    price_at_sale: float
    created_at: datetime

    class Config:
        from_attributes = True

class VisitPhotoResponse(BaseModel):
    id: str
    visit_id: str
    photo_url: str
    photo_file_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class VisitBase(BaseModel):
    center_id: Optional[str] = None
    region_id: Optional[str] = None
    brand_id: Optional[str] = None
    brand_ids: List[str] = []
    client_id: Optional[str] = None
    visit_date: datetime
    status: str = "planned"
    notes: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Doctor-visit flow
    visit_type: str = "center"               # 'center' | 'doctor'
    visit_reason: Optional[str] = None
    interested_product_ids: Optional[List[str]] = None

class StockCheckBase(BaseModel):
    visit_id: str
    product_id: Optional[str] = None
    competitor_product_name: Optional[str] = None
    observed_qty: int

class StockCheckCreate(StockCheckBase):
    pass

class StockCheckResponse(StockCheckBase):
    id: str
    synced: bool = False
    created_at: datetime
    
    class Config:
        from_attributes = True

class SpecialRequestCreate(BaseModel):
    id: str
    request_type: str
    description: Optional[str] = None

class SpecialRequestResponse(BaseModel):
    id: str
    visit_id: str
    request_type: str
    description: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class VisitCreate(VisitBase):
    id: str
    appointment_id: Optional[str] = None
    task_id: Optional[str] = None
    items: List[VisitItemCreate] = []
    arrival_time: Optional[datetime] = None
    completion_time: Optional[datetime] = None
    retroactive_reason: Optional[str] = None
    save_location_lat: Optional[float] = None
    save_location_lng: Optional[float] = None
    special_requests: List[SpecialRequestCreate] = []
    is_mocked: Optional[bool] = False

class VisitResponse(VisitBase):
    id: str
    appointment_id: Optional[str] = None
    reference_code: Optional[str] = None
    rep_id: str
    rep_name: Optional[str] = None
    rep_role: Optional[str] = None
    arrival_time: Optional[datetime] = None
    completion_time: Optional[datetime] = None
    synced: bool
    signature_url: Optional[str] = None
    signature_file_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    items: List[VisitItemResponse] = []
    photos: List[VisitPhotoResponse] = []
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_note: Optional[str] = None
    supervisor_note: Optional[str] = None
    retroactive_reason: Optional[str] = None
    save_location_lat: Optional[float] = None
    save_location_lng: Optional[float] = None
    special_requests: List[SpecialRequestCreate] = []
    visit_type: str = "center"
    visit_reason: Optional[str] = None
    interested_product_ids: Optional[List[str]] = None

    @field_validator("interested_product_ids", mode="before")
    @classmethod
    def _deserialize_interested_ids(cls, v):
        # Stored as JSON text in the DB; accept both str and list on read.
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return [s for s in v.split(",") if s]
        return v

    class Config:
        from_attributes = True

class VisitReviewRequest(BaseModel):
    status: str
    note: Optional[str] = None

class TargetBase(BaseModel):
    product_id: str
    rep_id: Optional[str] = None
    brand_id: Optional[str] = None
    period_start: datetime
    period_end: datetime
    target_qty: int

class TargetCreate(TargetBase):
    notes: Optional[str] = None

class TargetBulkCreate(BaseModel):
    product_id: str
    rep_ids: List[str]
    brand_id: Optional[str] = None
    period_start: datetime
    period_end: datetime
    target_qty: int
    notes: Optional[str] = None

class TargetUpdate(BaseModel):
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    target_qty: Optional[int] = None

class TargetResponse(TargetBase):
    id: str
    product_name: Optional[str] = None
    achieved_qty: int
    notes: Optional[str] = None
    view_count: int = 0
    user_viewed: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class TargetViewResponse(BaseModel):
    id: str
    target_id: str
    rep_id: str
    rep_name: Optional[str] = None
    viewed_at: datetime

    class Config:
        from_attributes = True

class AppointmentBase(BaseModel):
    rep_id: str
    client_id: Optional[str] = None
    center_id: Optional[str] = None
    appt_date: datetime
    appt_time: str
    reminder_minutes_before: int = 30
    notes: Optional[str] = None
    supervisor_note: Optional[str] = None
    suggested_product_id: Optional[str] = None
    status: str = "pending"

class AppointmentCreate(AppointmentBase):
    id: str

class AppointmentUpdate(BaseModel):
    client_id: Optional[str] = None
    appt_date: Optional[datetime] = None
    appt_time: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    supervisor_note: Optional[str] = None
    suggested_product_id: Optional[str] = None

class AppointmentResponse(AppointmentBase):
    id: str
    reference_code: Optional[str] = None
    center_name: Optional[str] = None
    client_name: Optional[str] = None
    suggested_product_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ClientCreate(BaseModel):
    id: str
    rep_id: Optional[str] = None
    facility_name: Optional[str] = None
    facility_type: Optional[str] = None
    doctor_name: Optional[str] = None
    specialty: Optional[str] = None
    birth_date: Optional[datetime] = None
    class_tier: Optional[str] = None
    relationship_type: Optional[str] = None
    description: Optional[str] = None
    phone_number: Optional[str] = None
    region: Optional[str] = None
    region_id: Optional[str] = None
    area: Optional[str] = None
    street: Optional[str] = None
    nearby_landmark: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    photo_url: Optional[str] = None
    # Doctor profiling (doctor-visit flow)
    gender: Optional[str] = None             # 'male' | 'female'
    rating: Optional[int] = None             # 1..5 stars
    treatment_quality: Optional[str] = None  # 'good' | 'average' | 'bad'
    
    # New fields
    brand_id: Optional[str] = None
    client_type: Optional[str] = "doctor"
    status: Optional[str] = "active"
    scientific_interests: Optional[str] = None
    product_interests: Optional[str] = None
    pharmacy_type: Optional[str] = None
    institution_type: Optional[str] = None
    key_contact_name: Optional[str] = None
    key_contact_position: Optional[str] = None
    key_contact_phone: Optional[str] = None
    departments: Optional[str] = None

class ClientUpdate(BaseModel):
    facility_name: Optional[str] = None
    facility_type: Optional[str] = None
    doctor_name: Optional[str] = None
    specialty: Optional[str] = None
    birth_date: Optional[datetime] = None
    class_tier: Optional[str] = None
    relationship_type: Optional[str] = None
    description: Optional[str] = None
    phone_number: Optional[str] = None
    region: Optional[str] = None
    region_id: Optional[str] = None
    area: Optional[str] = None
    street: Optional[str] = None
    nearby_landmark: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    photo_url: Optional[str] = None
    gender: Optional[str] = None
    rating: Optional[int] = None
    treatment_quality: Optional[str] = None
    client_type: Optional[str] = None
    status: Optional[str] = None
    scientific_interests: Optional[str] = None
    product_interests: Optional[str] = None
    pharmacy_type: Optional[str] = None
    institution_type: Optional[str] = None
    key_contact_name: Optional[str] = None
    key_contact_position: Optional[str] = None
    key_contact_phone: Optional[str] = None
    departments: Optional[str] = None

class ClientResponse(ClientCreate):
    photo_file_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Analytics Models
class RepPerformanceResponse(BaseModel):
    rep_id: str
    rep_name: str
    total_visits: int
    total_revenue: float
    rank: int

class ProductPerformanceResponse(BaseModel):
    product_id: str
    product_name: str
    units_sold: int
    total_revenue: float
    rank: int

class AnalyticsResponse(BaseModel):
    total_revenue: float
    total_visits: int
    visits_completed: int
    avg_visits_per_rep: float
    target_completion_percent: float
    top_products: List[ProductPerformanceResponse]
    top_reps: List[RepPerformanceResponse]

class RepReportResponse(BaseModel):
    rep_id: str
    rep_name: str
    rep_email: str
    rep_phone: Optional[str] = None
    rep_region: Optional[str] = None
    total_visits: int
    total_revenue: float
    total_sold_qty: int
    visits_completed: int
    visits_flagged: int
    visits_rejected: int
    target_qty: int
    achieved_qty: int
    target_completion_percent: float
    top_products: List[ProductPerformanceResponse]
    recent_visits: List[VisitResponse]
    period_start: str
    period_end: str

class ReviewNoteCreate(BaseModel):
    review_note: str

class SystemSettingBase(BaseModel):
    key: str
    value: str
    description: Optional[str] = None

class SystemSettingResponse(SystemSettingBase):
    updated_at: datetime

    class Config:
        from_attributes = True

class SystemSettingUpdate(BaseModel):
    value: str

class RegionCoverage(BaseModel):
    region: str
    visits_completed: int
    total_centers: int
    coverage_percent: float

class FCMTokenCreate(BaseModel):
    token: str

class DailyRevenueResponse(BaseModel):
    date: str
    revenue: float

class MonthlyRevenueResponse(BaseModel):
    month: str
    revenue: float

class SystemOverviewResponse(BaseModel):
    total_revenue: float
    total_users: int
    total_centers: int
    total_products: int
    visits_today: int
    low_stock_count: int
    pending_appointments: int
    region_coverage: List[RegionCoverage]

class ExpenseCreate(BaseModel):
    id: str
    visit_id: Optional[str] = None
    category: str
    amount: float
    description: Optional[str] = None

class ExpenseResponse(BaseModel):
    id: str
    visit_id: Optional[str] = None
    rep_id: str
    category: str
    amount: float
    description: Optional[str] = None
    receipt_image_url: Optional[str] = None
    receipt_file_id: Optional[str] = None
    status: str
    rejection_reason: Optional[str] = None
    requires_admin_approval: bool = False
    requires_gm_approval: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    rep_name: Optional[str] = None

    class Config:
        from_attributes = True

class ExpenseStatusUpdate(BaseModel):
    status: str
    rejection_reason: Optional[str] = None

class CheckInRequest(BaseModel):
    visit_id: str
    center_id: Optional[str] = None
    client_id: Optional[str] = None
    appointment_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy: Optional[float] = None
    is_retroactive: bool = False
    retroactive_reason: Optional[str] = None
    is_mocked: Optional[bool] = False

class FieldReportCreate(BaseModel):
    id: str
    rep_id: str
    content: str
    photo_url: Optional[str] = None

class FieldReportResponse(BaseModel):
    id: str
    rep_id: str
    content: str
    photo_url: Optional[str] = None
    photo_file_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class NoteCreate(BaseModel):
    content: str
    visit_id: Optional[str] = None

class NoteResponse(BaseModel):
    id: str
    sender_id: str
    sender_name: Optional[str] = None
    recipient_id: str
    recipient_name: Optional[str] = None
    visit_id: Optional[str] = None
    content: str
    created_at: datetime

    class Config:
        from_attributes = True

class ActivityLogResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    user_name: str
    action: str
    log_type: str
    related_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class TaskBase(BaseModel):
    rep_id: str
    region_id: Optional[str] = None
    brand_id: Optional[str] = None
    supervisor_id: Optional[str] = None
    task_type: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    quantity_target: Optional[int] = None
    due_date: Optional[datetime] = None
    status: str = 'pending'
    priority: str = 'normal'
    notes: Optional[str] = None
    purpose: Optional[str] = None
    visit_subtype: Optional[str] = None
    scheduled_datetime: Optional[datetime] = None
    rejection_report: Optional[str] = None
    reminder_offset: Optional[str] = None
    visit_started_at: Optional[datetime] = None
    visit_completed_at: Optional[datetime] = None

class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    status: Optional[str] = None
    completed_at: Optional[datetime] = None
    progress_note: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None
    rejection_report: Optional[str] = None
    reminder_offset: Optional[str] = None
    accepted_at: Optional[datetime] = None
    visit_id: Optional[str] = None
    visit_started_at: Optional[datetime] = None
    visit_completed_at: Optional[datetime] = None

class TaskResponse(TaskBase):
    id: str
    progress_note: Optional[str] = None
    is_deleted: bool = False
    created_at: datetime
    completed_at: Optional[datetime] = None
    rep_name: Optional[str] = None
    brand_name: Optional[str] = None
    brand_color: Optional[str] = None
    supervisor_name: Optional[str] = None
    purpose: Optional[str] = None
    visit_subtype: Optional[str] = None
    scheduled_datetime: Optional[datetime] = None
    rejection_report: Optional[str] = None
    reminder_offset: Optional[str] = None
    accepted_at: Optional[datetime] = None
    visit_id: Optional[str] = None
    
    class Config:
        from_attributes = True

class TaskHistoryResponse(BaseModel):
    id: str
    task_id: str
    action: str
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    changed_by: str
    changed_by_name: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True

class SearchTargetResponse(BaseModel):
    id: str
    name: str
    type: str  # doctor, pharmacy, institution, center
    region: Optional[str] = None
    region_id: Optional[str] = None

class BrandActivityLogBase(BaseModel):
    rep_id: Optional[str] = None
    brand_id: Optional[str] = None
    task_id: Optional[str] = None
    activity_type: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    notes: Optional[str] = None
    status: str = 'completed'

class BrandActivityLogCreate(BrandActivityLogBase):
    pass

class BrandActivityLogResponse(BrandActivityLogBase):
    id: str
    logged_at: datetime
    rep_name: Optional[str] = None
    brand_name: Optional[str] = None
    class Config:
        from_attributes = True

# ─────────────────────────────────────────────────────────────────────────────
# Directory & Reps schemas
# ─────────────────────────────────────────────────────────────────────────────
class DirectoryItemResponse(BaseModel):
    id: str
    type: str  # 'doctor' | 'pharmacy' | 'center'
    is_client: bool = True
    name: str
    region_id: Optional[str] = None
    region_name: Optional[str] = None
    area: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    specialty: Optional[str] = None
    facility_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    rep_id: Optional[str] = None
    rep_name: Optional[str] = None
    brand_id: Optional[str] = None
    status: Optional[str] = "active"
    profile_complete: bool = True
    doctor_name: Optional[str] = None
    client_type: Optional[str] = None
    facility_type: Optional[str] = None
    birth_date: Optional[datetime] = None
    class_tier: Optional[str] = None
    relationship_type: Optional[str] = None
    description: Optional[str] = None
    street: Optional[str] = None
    nearby_landmark: Optional[str] = None
    photo_url: Optional[str] = None
    gender: Optional[str] = None
    rating: Optional[int] = None
    treatment_quality: Optional[str] = None
    scientific_interests: Optional[str] = None
    product_interests: Optional[str] = None
    pharmacy_type: Optional[str] = None
    institution_type: Optional[str] = None
    key_contact_name: Optional[str] = None
    key_contact_position: Optional[str] = None
    key_contact_phone: Optional[str] = None
    departments: Optional[str] = None

class RepItemResponse(BaseModel):
    id: str
    full_name: str
    email: str
    phone: Optional[str] = None
    profile_image_url: Optional[str] = None
    region: Optional[str] = None
    region_id: Optional[str] = None
    region_name: Optional[str] = None
    is_active: bool
    brand_id: Optional[str] = None
    supervisor_id: Optional[str] = None
    last_lat: Optional[float] = None
    last_lng: Optional[float] = None
    last_location_update: Optional[datetime] = None

class RepVisitedRegion(BaseModel):
    region_id: Optional[str] = None
    region_name: str
    visits_count: int = 0
    locations: List[dict] = []

class RepOverviewResponse(BaseModel):
    rep: RepItemResponse
    visits: dict
    visited_regions: List[RepVisitedRegion]
    completed_tasks: dict
    sales: dict
    recent_activity: List[dict] = []
