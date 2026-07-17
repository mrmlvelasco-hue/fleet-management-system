"""Registration Template — the same PMS-style pattern used for Maintenance
(recurring interval + checklist + generation policy + due-calculation),
applied to Vehicle Registration renewal. Deliberately simpler than the
Maintenance PMS engine: calendar-only (no KM dimension — registration
renewal doesn't depend on odometer), and checklist items live directly on
the template rather than needing a separate profile-grouping layer, since
a vehicle only has one registration cycle at a time (unlike maintenance,
where several different PM packages can apply to the same vehicle).
"""
from app.extensions import db
from app.core.models.base import BaseModel


class RegistrationTemplate(db.Model, BaseModel):
    __tablename__ = "registration_templates"

    vehicle_type_id = db.Column(db.Integer, db.ForeignKey("vehicle_types.id"),
                                nullable=True, index=True)  # NULL = all types
    vehicle_brand_id = db.Column(db.Integer, db.ForeignKey("vehicle_brands.id"),
                                 nullable=True, index=True)
    vehicle_model_id = db.Column(db.Integer, db.ForeignKey("vehicle_models.id"),
                                 nullable=True, index=True)

    interval_years = db.Column(db.Integer, nullable=False, default=3)
    # MANUAL | AUTO_SCHEDULE (default, recommended — notify only) |
    # AUTO_REGISTRATION (auto-creates a DRAFT renewal transaction)
    next_generation_policy = db.Column(db.String(20), nullable=False,
                                       default="AUTO_SCHEDULE")
    notify_before_days = db.Column(db.Integer, nullable=True)  # falls back
                                                                # to SystemParameters
    priority = db.Column(db.String(10), nullable=False, default="MEDIUM")

    vehicle_type = db.relationship("VehicleType")
    vehicle_brand = db.relationship("VehicleBrand")
    vehicle_model_ref = db.relationship("VehicleModel")
    checklist_items = db.relationship(
        "RegistrationChecklistItem", backref="template",
        order_by="RegistrationChecklistItem.sort_order",
        cascade="all, delete-orphan")


class RegistrationChecklistItem(db.Model, BaseModel):
    __tablename__ = "registration_checklist_items"
    template_id = db.Column(db.Integer, db.ForeignKey("registration_templates.id"),
                            nullable=False, index=True)
    activity_code = db.Column(db.String(40), nullable=False)
    activity_description = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
