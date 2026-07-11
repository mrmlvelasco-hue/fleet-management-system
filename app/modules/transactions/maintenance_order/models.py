"""Maintenance Order transaction model (Preventive + Corrective) with an
optional generated checklist copied from a PM Scope Template."""
from app.extensions import db
from app.core.models.base import BaseModel


class MaintenanceOrder(db.Model, BaseModel):
    __tablename__ = "maintenance_orders"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False)
    maintenance_type_id = db.Column(db.Integer,
                                    db.ForeignKey("maintenance_types.id"),
                                    nullable=False)
    # PREVENTIVE | CORRECTIVE — copied from MaintenanceType at creation time
    category = db.Column(db.String(12), nullable=False)
    pm_schedule_id = db.Column(db.Integer, db.ForeignKey("pm_schedules.id"),
                               nullable=True)
    scope_template_id = db.Column(db.Integer,
                                  db.ForeignKey("pm_scope_templates.id"),
                                  nullable=True)
    description = db.Column(db.Text, nullable=True)
    odometer_at_service = db.Column(db.Integer, nullable=True)
    scheduled_date = db.Column(db.Date, nullable=False)
    completed_date = db.Column(db.Date, nullable=True)
    assigned_mechanic = db.Column(db.String(120), nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=True)
    estimated_cost = db.Column(db.Numeric(18, 2), nullable=True)
    actual_cost = db.Column(db.Numeric(18, 2), nullable=True)

    # DRAFT | PENDING | APPROVED | IN_PROGRESS | COMPLETED | CANCELLED —
    # physical lifecycle, separate from the linked ApprovalInstance.status.
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    vehicle = db.relationship("Vehicle")
    maintenance_type = db.relationship("MaintenanceType")
    pm_schedule = db.relationship("PMSchedule")
    scope_template = db.relationship("PMScopeTemplate")
    vendor = db.relationship("Vendor")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
    checklist_items = db.relationship(
        "MaintenanceChecklistItem", backref="order",
        order_by="MaintenanceChecklistItem.sort_order",
        cascade="all, delete-orphan")


class MaintenanceChecklistItem(db.Model, BaseModel):
    __tablename__ = "maintenance_checklist_items"
    order_id = db.Column(db.Integer, db.ForeignKey("maintenance_orders.id"),
                         nullable=False)
    activity_code = db.Column(db.String(40), nullable=False)
    activity_description = db.Column(db.String(255), nullable=False)
    is_done = db.Column(db.Boolean, default=False, nullable=False)
    done_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    done_at = db.Column(db.DateTime, nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    done_by_user = db.relationship("User")
