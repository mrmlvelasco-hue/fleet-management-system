"""Reference master data: VehicleType, MaintenanceType."""
from app.extensions import db
from app.core.models.base import BaseModel


class VehicleType(db.Model, BaseModel):
    __tablename__ = "vehicle_types"
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    # LIGHT | HEAVY | MOTORCYCLE | SPECIAL (from Lookup VEHICLE_CATEGORY)
    category = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(255))


class MaintenanceType(db.Model, BaseModel):
    __tablename__ = "maintenance_types"
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    # PREVENTIVE | CORRECTIVE | PREDICTIVE — this exact value drives
    # MaintenanceOrderService's checklist-required rule, so it's a fixed
    # dropdown in the UI, not free text or Lookup-driven.
    category = db.Column(db.String(20), nullable=False)
    # DEPRECATED: superseded by PMSchedule.interval_km/interval_days (added
    # in the Phase 3b PM Template revision), which is Make/Model-aware and
    # is what PMDueCalculationService actually reads. These columns are
    # kept (nullable, unused) only for backward DB compatibility — the UI
    # no longer exposes them; configure intervals via PM Templates instead.
    interval_km = db.Column(db.Integer, nullable=True)
    interval_days = db.Column(db.Integer, nullable=True)
    description = db.Column(db.String(255))
