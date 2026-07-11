"""Purchase Request transaction model — the document type that actually
exercises the Approval Matrix's amount-range resolution (has a real
monetary amount, computed as the sum of its line items)."""
from app.extensions import db
from app.core.models.base import BaseModel


class PurchaseRequest(db.Model, BaseModel):
    __tablename__ = "purchase_requests"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"),
                              nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=True)
    amount = db.Column(db.Numeric(18, 2), default=0, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    justification = db.Column(db.Text, nullable=True)
    needed_by_date = db.Column(db.Date, nullable=True)

    # DRAFT | PENDING | APPROVED | ORDERED | RECEIVED | REJECTED | RETURNED
    # | CANCELLED — physical lifecycle, separate from ApprovalInstance.status.
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    department = db.relationship("Department")
    vendor = db.relationship("Vendor")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
    lines = db.relationship("PurchaseRequestLine", backref="request",
                            order_by="PurchaseRequestLine.id",
                            cascade="all, delete-orphan")


class PurchaseRequestLine(db.Model, BaseModel):
    __tablename__ = "purchase_request_lines"
    pr_id = db.Column(db.Integer, db.ForeignKey("purchase_requests.id"),
                      nullable=False)
    item_description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), nullable=False, default=1)
    unit_cost = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    line_total = db.Column(db.Numeric(18, 2), nullable=False, default=0)
