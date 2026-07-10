"""Shared base for transaction module services.

Every transaction module (Trip Ticket, ATD, Vehicle Movement, and the
7 more in 3b/3c) follows the same submit/approve/reject/return/cancel
recipe: create a DRAFT record, submit it through the generic ApprovalEngine
(no approval logic here), and keep only the module's own *physical*
status (DRAFT/RELEASED/COMPLETED/etc.) in sync — the approval workflow
status itself lives entirely on the ApprovalInstance.
"""
from app.extensions import db
from app.core.approval.engine import ApprovalEngine


class BaseTransactionService:
    model = None              # subclass sets: the SQLAlchemy model
    document_type_code = None  # subclass sets: e.g. "TT", "ATD", "VM"
    reference_table = None    # subclass sets: e.g. "trip_tickets"

    def __init__(self):
        self.engine = ApprovalEngine()

    def submit(self, record_id: int, user):
        """Submit a DRAFT record through the Approval Engine."""
        record = db.session.get(self.model, record_id)
        instance = self.engine.submit(
            self.document_type_code, self.reference_table, record_id,
            amount=getattr(record, "amount", None), user=user)
        record.approval_instance_id = instance.id
        db.session.commit()
        return record

    def approve(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        self.engine.approve(record.approval_instance, user, remarks)
        db.session.commit()
        return record

    def reject(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        self.engine.reject(record.approval_instance, user, remarks)
        db.session.commit()
        return record

    def return_document(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        self.engine.return_document(record.approval_instance, user, remarks)
        db.session.commit()
        return record

    def resubmit(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        self.engine.resubmit(record.approval_instance, user, remarks)
        db.session.commit()
        return record

    def cancel(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        if record.approval_instance_id:
            self.engine.cancel(record.approval_instance, user, remarks)
        record.status = "CANCELLED"
        db.session.commit()
        return record

    def list(self, include_inactive: bool = True):
        query = db.session.query(self.model)
        if not include_inactive:
            query = query.filter(self.model.is_active.is_(True))
        return query.order_by(self.model.id.desc()).all()

    def get(self, record_id: int):
        return db.session.get(self.model, record_id)
