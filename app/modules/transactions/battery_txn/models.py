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
    # MOUNT | DISMOUNT | DISPOSE
    action = db.Column(db.String(10), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    remarks = db.Column(db.Text, nullable=True)

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
