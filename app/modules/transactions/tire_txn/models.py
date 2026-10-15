"""Tire Management transaction: mount/dismount/retread/dispose events
against a Tire master record."""
from app.extensions import db
from app.core.models.base import BaseModel


class TireTransaction(db.Model, BaseModel):
    __tablename__ = "tire_transactions"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    tire_id = db.Column(db.Integer, db.ForeignKey("tires.id"), nullable=False)
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

    # DRAFT | COMPLETED | CANCELLED
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    tire = db.relationship("Tire")
    vehicle = db.relationship("Vehicle")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
    maintenance_order = db.relationship("MaintenanceOrder")
