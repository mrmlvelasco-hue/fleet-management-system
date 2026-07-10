"""Authority To Drive (ATD) transaction model."""
from app.extensions import db
from app.core.models.base import BaseModel


class AuthorityToDrive(db.Model, BaseModel):
    __tablename__ = "authority_to_drives"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
                          nullable=False)
    purpose = db.Column(db.String(255), nullable=False)
    valid_from = db.Column(db.Date, nullable=False)
    valid_to = db.Column(db.Date, nullable=False)

    # DRAFT | ACTIVE | EXPIRED | CANCELLED — physical lifecycle, separate
    # from the linked ApprovalInstance.status (approval workflow state).
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    vehicle = db.relationship("Vehicle")
    driver = db.relationship("Driver")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
