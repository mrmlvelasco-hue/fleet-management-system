"""Runtime approval models: one ApprovalInstance per submitted document,
one ApprovalAction row per state change."""
from datetime import datetime, timezone

from app.extensions import db
from app.core.models.base import BaseModel


class ApprovalInstance(db.Model, BaseModel):
    __tablename__ = "approval_instances"
    document_type_id = db.Column(db.Integer, db.ForeignKey("document_types.id"),
                                 nullable=False)
    approval_path_id = db.Column(db.Integer, db.ForeignKey("approval_paths.id"),
                                 nullable=True)  # NULL when auto-approved
    reference_table = db.Column(db.String(100), nullable=False, index=True)
    reference_id = db.Column(db.Integer, nullable=False, index=True)
    amount = db.Column(db.Numeric(18, 2), nullable=True)
    current_level = db.Column(db.Integer, default=0, nullable=False)
    # DRAFT | PENDING | APPROVED | REJECTED | RETURNED | CANCELLED
    status = db.Column(db.String(12), default="DRAFT", nullable=False, index=True)
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    document_type = db.relationship("DocumentType")
    approval_path = db.relationship("ApprovalPath")
    actions = db.relationship("ApprovalAction", backref="instance",
                              order_by="ApprovalAction.id")


class ApprovalAction(db.Model, BaseModel):
    __tablename__ = "approval_actions"
    instance_id = db.Column(db.Integer, db.ForeignKey("approval_instances.id"),
                            nullable=False)
    level_number = db.Column(db.Integer, nullable=False)
    # SUBMIT | APPROVE | REJECT | RETURN | CANCEL
    action = db.Column(db.String(10), nullable=False)
    acted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    remarks = db.Column(db.String(500))
    acted_at = db.Column(db.DateTime,
                         default=lambda: datetime.now(timezone.utc),
                         nullable=False)
