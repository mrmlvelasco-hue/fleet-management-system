"""PM Configuration models: schedules (when maintenance is due) and scope
templates (what gets done / the checklist)."""
from app.extensions import db
from app.core.models.base import BaseModel


class PMSchedule(db.Model, BaseModel):
    __tablename__ = "pm_schedules"
    vehicle_type_id = db.Column(db.Integer, db.ForeignKey("vehicle_types.id"),
                                nullable=True)  # NULL = applies to all types
    # vehicle_make/vehicle_model: free-text match against Vehicle.brand/model
    # (case-insensitive). When set, takes precedence over vehicle_type_id —
    # this is what lets different manufacturers have different PM intervals
    # for the "same" vehicle type/category. Kept for backward compatibility;
    # vehicle_brand_id/vehicle_model_id below is the preferred, referentially
    # -correct alternative going forward (PMS-1).
    vehicle_make = db.Column(db.String(80), nullable=True)
    vehicle_model = db.Column(db.String(80), nullable=True)
    # Real FK match against the VehicleBrand/VehicleModel master tables —
    # takes precedence over the free-text fields above when set. Avoids the
    # spelling-variation risk of free-text matching (e.g. "Toyota" vs
    # "TOYOTA").
    vehicle_brand_id = db.Column(db.Integer, db.ForeignKey("vehicle_brands.id"),
                                 nullable=True)
    vehicle_model_id = db.Column(db.Integer, db.ForeignKey("vehicle_models.id"),
                                 nullable=True)
    # Additional matching/identification dimensions (all optional —
    # narrows the match further when set, ignored when NULL).
    variant = db.Column(db.String(80), nullable=True)
    engine_type = db.Column(db.String(80), nullable=True)
    fuel_type = db.Column(db.String(20), nullable=True)
    transmission = db.Column(db.String(40), nullable=True)
    model_year_from = db.Column(db.Integer, nullable=True)
    model_year_to = db.Column(db.Integer, nullable=True)
    # Human-facing PMS Profile identification.
    profile_code = db.Column(db.String(40), unique=True, nullable=True)
    profile_description = db.Column(db.String(255), nullable=True)
    effective_date = db.Column(db.Date, nullable=True)

    maintenance_type_id = db.Column(db.Integer,
                                    db.ForeignKey("maintenance_types.id"),
                                    nullable=False)
    # KM | CALENDAR | HYBRID (whichever comes first)
    trigger_mode = db.Column(db.String(10), nullable=False, default="HYBRID")
    interval_km = db.Column(db.Integer, nullable=True)
    interval_days = db.Column(db.Integer, nullable=True)
    priority = db.Column(db.String(10), default="MEDIUM", nullable=False)

    # Per-template alert overrides — fall back to the global SystemParameters
    # PM_DUE_SOON_KM / PM_DUE_SOON_DAYS when NULL.
    notify_before_km = db.Column(db.Integer, nullable=True)
    notify_before_days = db.Column(db.Integer, nullable=True)
    escalate_if_overdue = db.Column(db.Boolean, default=True, nullable=False)

    vehicle_type = db.relationship("VehicleType")
    vehicle_brand = db.relationship("VehicleBrand")
    vehicle_model_ref = db.relationship("VehicleModel")
    maintenance_type = db.relationship("MaintenanceType",
                                       backref="pm_schedules")
    scope_templates = db.relationship("PMScopeTemplate", backref="pm_schedule")


class PMScopeTemplate(db.Model, BaseModel):
    __tablename__ = "pm_scope_templates"
    maintenance_type_id = db.Column(db.Integer,
                                    db.ForeignKey("maintenance_types.id"),
                                    nullable=False)
    # Optional direct link to one specific PM Template (PMSchedule) — lets
    # "Honda City 10,000 KM PMS" and "Toyota Hilux 10,000 KM PMS" have
    # different checklists even though both nominally share the same
    # maintenance_type. NULL = generic template matched by maintenance_type
    # only (backward-compatible fallback).
    pm_schedule_id = db.Column(db.Integer, db.ForeignKey("pm_schedules.id"),
                               nullable=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))

    maintenance_type = db.relationship("MaintenanceType")
    items = db.relationship("PMScopeItem", backref="template",
                            order_by="PMScopeItem.sort_order",
                            cascade="all, delete-orphan")


class PMScopeItem(db.Model, BaseModel):
    __tablename__ = "pm_scope_items"
    template_id = db.Column(db.Integer, db.ForeignKey("pm_scope_templates.id"),
                            nullable=False)
    activity_code = db.Column(db.String(40), nullable=False)
    activity_description = db.Column(db.String(255), nullable=False)
    standard_labor_hours = db.Column(db.Numeric(6, 2), nullable=True)
    estimated_cost = db.Column(db.Numeric(18, 2), nullable=True)
    required_parts = db.Column(db.Text, nullable=True)
    vendor_recommendation = db.Column(db.String(120), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
