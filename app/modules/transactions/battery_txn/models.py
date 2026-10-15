"""Battery Management transaction: mount/dismount/dispose events against a
Battery master record."""
from app.extensions import db
from app.core.models.base import BaseModel


class BatteryTransaction(db.Model, BaseModel):
    __tablename__ = "battery_transactions"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    battery_id = db.Column(db.Integer, db.ForeignKey("batteries.id"),
                           nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=True)  # NULL when dismounted to stock
    # MOUNT | DISMOUNT | DISPOSE | TEST
    action = db.Column(db.String(10), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    remarks = db.Column(db.Text, nullable=True)
    odometer_at_service = db.Column(db.Integer, nullable=True)
    # TEST readings: voltage and GOOD | WEAK | REPLACE.
    voltage = db.Column(db.Numeric(5, 2), nullable=True)
    test_result = db.Column(db.String(10), nullable=True)
    # Same cost / source / MO rules as TireTransaction.
    cost = db.Column(db.Numeric(18, 2), nullable=True)
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

    battery = db.relationship("Battery")
    vehicle = db.relationship("Vehicle")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
    maintenance_order = db.relationship("MaintenanceOrder")
