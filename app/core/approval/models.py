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
    # Organizational context of the transaction being approved — supplied
    # by the submitting module (same pattern as `amount`). NULL means "no
    # org context recorded", in which case eligibility falls back to
    # role-only matching for full backward compatibility.
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=True)
    business_unit_id = db.Column(db.Integer,
                                 db.ForeignKey("business_units.id"),
                                 nullable=True)
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


class ApprovalTask(db.Model, BaseModel):
    """Generic Approval Task / Inbox entity (F2) — one row per pending (or
    completed/cancelled) approval level, across every module. This is the
    single, reusable query behind the "For My Action" worklist; no module
    needs its own pending-approval query. Fully managed by the
    ApprovalEngine — never created/updated directly by business modules.
    """
    __tablename__ = "approval_tasks"
    approval_instance_id = db.Column(db.Integer,
                                     db.ForeignKey("approval_instances.id"),
                                     nullable=False)
    level_number = db.Column(db.Integer, nullable=False)
    document_type_id = db.Column(db.Integer, db.ForeignKey("document_types.id"),
                                 nullable=False)
    document_number = db.Column(db.String(40), nullable=True)
    reference_table = db.Column(db.String(100), nullable=False)
    reference_id = db.Column(db.Integer, nullable=False)
    assigned_role_id = db.Column(db.Integer, db.ForeignKey("roles.id"),
                                 nullable=True)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                                 nullable=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=True)
    business_unit_id = db.Column(db.Integer,
                                 db.ForeignKey("business_units.id"),
                                 nullable=True)
    # PENDING | COMPLETED | CANCELLED
    status = db.Column(db.String(12), default="PENDING", nullable=False,
                      index=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    completed_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)

    instance = db.relationship("ApprovalInstance", backref="tasks")
    document_type = db.relationship("DocumentType")
    assigned_role = db.relationship("Role")
    assigned_user = db.relationship("User", foreign_keys=[assigned_user_id])
    requester = db.relationship("User", foreign_keys=[requested_by])
