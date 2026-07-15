"""Generic document comment/discussion thread.

Same cross-cutting pattern as Attachment/AuditLog/ApprovalInstance —
reference_table + reference_id, no per-module comment tables needed. Any
requester or approver can post a comment on any document they can view,
optionally @-directed at a specific recipient, so approval conversations
don't have to happen over email/chat outside the system.
"""
from app.extensions import db
from app.core.models.base import BaseModel


class DocumentComment(db.Model, BaseModel):
    __tablename__ = "document_comments"
    reference_table = db.Column(db.String(100), nullable=False, index=True)
    reference_id = db.Column(db.Integer, nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    body = db.Column(db.Text, nullable=False)

    author = db.relationship("User", foreign_keys=[author_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])
