"""Maintenance Order transaction model (Preventive + Corrective) with an
optional generated checklist copied from a PM Scope Template.
"""
from app.extensions import db
from app.core.models.base import BaseModel


class TransactionType(db.Model, BaseModel):
    """Per the MO Module Enhancement spec: 'Every Transaction Type
    belongs to exactly one Category.' Admin-configurable master data —
    not hardcoded, so new operational transaction types can be added
    without a code change. `group` is purely for organizing the New MO
    form's dropdown into optgroups (Deployment/Administrative/Disposal/
    Accessories/Maintenance) — it isn't a third hierarchy level, just a
    display aid, since a flat list of 40+ types in one dropdown would be
    unusable."""
    __tablename__ = "mo_transaction_types"

    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    # MAINTENANCE | OPERATIONAL
    order_category = db.Column(db.String(15), nullable=False)
    # DEPLOYMENT | ADMINISTRATIVE | DISPOSAL | ACCESSORIES | MAINTENANCE
    group = db.Column(db.String(20), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class MaintenanceOrder(db.Model, BaseModel):
    __tablename__ = "maintenance_orders"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False)
    # Operational-category orders (Deployment, Administrative, Disposal,
    # Accessories) are valid work requests that aren't vehicle
    # maintenance at all — so unlike before, this is now nullable.
    maintenance_type_id = db.Column(db.Integer,
                                    db.ForeignKey("maintenance_types.id"),
                                    nullable=True)
    # PREVENTIVE | CORRECTIVE — copied from MaintenanceType at creation
    # time. Nullable for the same reason as maintenance_type_id above.
    category = db.Column(db.String(12), nullable=True)
    # MAINTENANCE | OPERATIONAL — the top-level split from the MO Module
    # Enhancement spec. Defaults to MAINTENANCE so every existing order
    # (and every order created the same way as before) is unaffected.
    order_category = db.Column(db.String(15), nullable=False,
                               default="MAINTENANCE")
    transaction_type_id = db.Column(db.Integer,
                                    db.ForeignKey("mo_transaction_types.id"),
                                    nullable=True)
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
    # Used by Operational/Deployment orders (Assignment, Reassignment,
    # Relocation, Transfer) as the "Vehicle Assignment Memo" — the formal
    # paper trail for a driver assignment change. On completion, this
    # updates Vehicle.assigned_driver_id the same way an approved ATD
    # does (see assignment_hooks.py) -- either mechanism can be the
    # operative record depending on which document your process uses.
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
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
    transaction_type = db.relationship("TransactionType")
    pm_schedule = db.relationship("PMSchedule")
    scope_template = db.relationship("PMScopeTemplate")
    vendor = db.relationship("Vendor")
    driver = db.relationship("Driver")
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
