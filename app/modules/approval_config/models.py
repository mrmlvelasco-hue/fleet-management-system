"""Approval Path / Level / Matrix models (configuration side of the engine)."""
from app.extensions import db
from app.core.models.base import BaseModel


class ApprovalPath(db.Model, BaseModel):
    __tablename__ = "approval_paths"
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(255))
    levels = db.relationship("ApprovalLevel", backref="path",
                             order_by="ApprovalLevel.level_number",
                             cascade="all, delete-orphan")


class ApprovalLevel(db.Model, BaseModel):
    __tablename__ = "approval_levels"
    path_id = db.Column(db.Integer, db.ForeignKey("approval_paths.id"),
                        nullable=False)
    level_number = db.Column(db.Integer, nullable=False)
    # ROLE: any active user holding role_id may act; USER: only user_id may act.
    approver_type = db.Column(db.String(10), nullable=False, default="ROLE")
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    role = db.relationship("Role")
    user = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("path_id", "level_number", name="uq_path_level"),
    )


class ApprovalMatrix(db.Model, BaseModel):
    __tablename__ = "approval_matrices"
    document_type_id = db.Column(db.Integer, db.ForeignKey("document_types.id"),
                                 nullable=False)
    approval_path_id = db.Column(db.Integer, db.ForeignKey("approval_paths.id"),
                                 nullable=False)
    # Both NULL = amount-independent matrix (Trip Ticket, ATD, ...)
    min_amount = db.Column(db.Numeric(18, 2), nullable=True)
    max_amount = db.Column(db.Numeric(18, 2), nullable=True)
    effective_from = db.Column(db.Date, nullable=True)
    effective_to = db.Column(db.Date, nullable=True)

    document_type = db.relationship("DocumentType")
    approval_path = db.relationship("ApprovalPath")
