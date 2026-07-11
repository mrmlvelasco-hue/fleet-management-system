"""PM Configuration models: schedules (when maintenance is due) and scope
templates (what gets done / the checklist)."""
from app.extensions import db
from app.core.models.base import BaseModel


class PMSchedule(db.Model, BaseModel):
    __tablename__ = "pm_schedules"
    vehicle_type_id = db.Column(db.Integer, db.ForeignKey("vehicle_types.id"),
                                nullable=True)  # NULL = applies to all types
    maintenance_type_id = db.Column(db.Integer,
                                    db.ForeignKey("maintenance_types.id"),
                                    nullable=False)
    # KM | CALENDAR | HYBRID (whichever comes first)
    trigger_mode = db.Column(db.String(10), nullable=False, default="HYBRID")
    interval_km = db.Column(db.Integer, nullable=True)
    interval_days = db.Column(db.Integer, nullable=True)
    priority = db.Column(db.String(10), default="MEDIUM", nullable=False)

    vehicle_type = db.relationship("VehicleType")
    maintenance_type = db.relationship("MaintenanceType")


class PMScopeTemplate(db.Model, BaseModel):
    __tablename__ = "pm_scope_templates"
    maintenance_type_id = db.Column(db.Integer,
                                    db.ForeignKey("maintenance_types.id"),
                                    nullable=False)
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
