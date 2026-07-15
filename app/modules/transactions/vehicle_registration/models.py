"""Vehicle Registration transaction model — Philippine LTO rules: 3-year
registration for new vehicles, Conduction Number before Plate Number,
renewal reminders."""
from app.extensions import db
from app.core.models.base import BaseModel


class VehicleRegistration(db.Model, BaseModel):
    __tablename__ = "vehicle_registrations"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False)
    # NEW | RENEWAL
    registration_type = db.Column(db.String(10), nullable=False)
    or_number = db.Column(db.String(60), nullable=True)  # Official Receipt
    cr_number = db.Column(db.String(60), nullable=True)  # Certificate of Reg.
    plate_number = db.Column(db.String(20), nullable=True)
    registration_date = db.Column(db.Date, nullable=True)
    validity_years = db.Column(db.Integer, nullable=False, default=3)
    expiry_date = db.Column(db.Date, nullable=True)
    or_cr_cost = db.Column(db.Numeric(18, 2), nullable=True)
    odometer_at_registration = db.Column(db.Integer, nullable=True)

    # DRAFT | PENDING | APPROVED | COMPLETED | CANCELLED
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    vehicle = db.relationship("Vehicle")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
