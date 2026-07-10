"""Trip Ticket transaction model.

Supports two driver modes per SystemParameter REQUIRE_DRIVER_FROM_MASTER:
YES -> driver_id (Driver master record) required.
NO  -> driver_name_manual (free text) required; no master record created.
"""
from app.extensions import db
from app.core.models.base import BaseModel


class TripTicket(db.Model, BaseModel):
    __tablename__ = "trip_tickets"

    document_number = db.Column(db.String(40), unique=True, nullable=True, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"), nullable=True)
    driver_name_manual = db.Column(db.String(150), nullable=True)

    destination = db.Column(db.String(255), nullable=False)
    purpose = db.Column(db.String(255), nullable=False)
    departure_datetime = db.Column(db.DateTime, nullable=False)
    return_datetime = db.Column(db.DateTime, nullable=True)
    odometer_out = db.Column(db.Integer, nullable=True)
    odometer_in = db.Column(db.Integer, nullable=True)
    passengers = db.Column(db.Text, nullable=True)

    # Physical trip lifecycle — separate from the approval workflow status.
    # DRAFT | RELEASED | RETURNED | CANCELLED
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    vehicle = db.relationship("Vehicle")
    driver = db.relationship("Driver")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
