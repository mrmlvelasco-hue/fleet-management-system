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
    # MOUNT | DISMOUNT | RETREAD | DISPOSE
    action = db.Column(db.String(10), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    odometer_at_service = db.Column(db.Integer, nullable=True)
    remarks = db.Column(db.Text, nullable=True)

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
