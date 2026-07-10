"""Vehicle Movement transaction model (transfer/dispatch/return between
locations, branches, or custody)."""
from app.extensions import db
from app.core.models.base import BaseModel


class VehicleMovement(db.Model, BaseModel):
    __tablename__ = "vehicle_movements"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False)
    # Lookup MOVEMENT_TYPE: TRANSFER | DISPATCH | RETURN | OTHER
    movement_type = db.Column(db.String(20), nullable=False)
    from_location = db.Column(db.String(255), nullable=False)
    to_location = db.Column(db.String(255), nullable=False)
    movement_date = db.Column(db.Date, nullable=False)
    remarks = db.Column(db.Text, nullable=True)

    # DRAFT | IN_TRANSIT | COMPLETED | CANCELLED — physical lifecycle,
    # separate from the linked ApprovalInstance.status.
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    vehicle = db.relationship("Vehicle")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
