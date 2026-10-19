"""Tire Management transaction: mount/dismount/retread/dispose events
against a Tire master record."""
from app.extensions import db
from app.core.models.base import BaseModel


class TireTransaction(db.Model, BaseModel):
    __tablename__ = "tire_transactions"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    # The tire acted on -- for a REPLACE, the one that goes ON. NULL only
    # while a REPLACE with a vendor-supplied new tire is awaiting
    # approval: the tire is registered when the document completes.
    tire_id = db.Column(db.Integer, db.ForeignKey("tires.id"), nullable=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=True)  # NULL when dismounted to stock
    # MOUNT | DISMOUNT | RETREAD | DISPOSE | ROTATE | INSPECT | REPAIR
    action = db.Column(db.String(10), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    odometer_at_service = db.Column(db.Integer, nullable=True)
    remarks = db.Column(db.Text, nullable=True)
    # Where on the vehicle: the wheel it was mounted on / rotated TO /
    # inspected at. WHEEL_POSITION lookup code.
    wheel_position = db.Column(db.String(20), nullable=True)
    # Readings (INSPECT / REPAIR / MOUNT of a new tire).
    tread_depth_mm = db.Column(db.Numeric(5, 2), nullable=True)
    pressure_psi = db.Column(db.Integer, nullable=True)
    # Standalone transactions carry their own cost. MO-sourced ones leave
    # it empty: the MO (parts / invoices) is the cost, counted once.
    cost = db.Column(db.Numeric(18, 2), nullable=True)
    # MANUAL | MO | CHECKLIST -- where this transaction came from.
    source = db.Column(db.String(12), nullable=False, default="MANUAL",
                       server_default="MANUAL")
    maintenance_order_id = db.Column(
        db.Integer, db.ForeignKey("maintenance_orders.id"), nullable=True,
        index=True)

    # ── REPLACE as one document (2026-10-19) ─────────────────────────
    # The tire that came off, why, and where it went.
    removed_tire_id = db.Column(db.Integer, db.ForeignKey("tires.id"),
                                nullable=True)
    removal_reason = db.Column(db.String(40), nullable=True)
    # FOR_DISPOSAL | REPAIRABLE (both go back to stock as IN_STOCK).
    removed_to = db.Column(db.String(15), nullable=True)
    # Where the new one came from: VENDOR (registered from new_* when the
    # document completes) | STOCK (tire_id, IN_STOCK).
    install_source = db.Column(db.String(10), nullable=True)
    new_serial = db.Column(db.String(60), nullable=True)
    new_brand = db.Column(db.String(80), nullable=True)
    new_model = db.Column(db.String(80), nullable=True)
    new_size = db.Column(db.String(30), nullable=True)
    # Who did the work, and the labour on top of `cost` (the tire).
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=True)
    technician = db.Column(db.String(120), nullable=True)
    labor_cost = db.Column(db.Numeric(18, 2), nullable=True)

    # DRAFT | COMPLETED | CANCELLED
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    tire = db.relationship("Tire", foreign_keys=[tire_id])
    removed_tire = db.relationship("Tire", foreign_keys=[removed_tire_id])
    vendor = db.relationship("Vendor")
    scope_items = db.relationship(
        "TireTransactionScopeItem", order_by="TireTransactionScopeItem.sort_order",
        cascade="all, delete-orphan")
    vehicle = db.relationship("Vehicle")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
    maintenance_order = db.relationship("MaintenanceOrder")


class TireTransactionScopeItem(db.Model, BaseModel):
    """Scope of Work on a standalone tire transaction -- the same
    activity checklist the MO uses, pre-filled from a tire PM Scope
    Template and editable per document."""
    __tablename__ = "tire_transaction_scope_items"
    transaction_id = db.Column(db.Integer,
                               db.ForeignKey("tire_transactions.id"),
                               nullable=False, index=True)
    activity_description = db.Column(db.String(255), nullable=False)
    is_done = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
