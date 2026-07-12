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


class NotVisibleError(Exception):
    """Raised when a user attempts to act on a record outside their
    organizational scope and who isn't its own requester. Distinct from
    NotEligibleApproverError (which governs approval actions specifically,
    already enforced by the ApprovalEngine's own eligibility check) — this
    guards the actions that had no scope protection at all: submit,
    resubmit, cancel."""
    pass


class BaseTransactionService:
    model = None              # subclass sets: the SQLAlchemy model
    document_type_code = None  # subclass sets: e.g. "TT", "ATD", "VM"
    reference_table = None    # subclass sets: e.g. "trip_tickets"

    def __init__(self):
        self.engine = ApprovalEngine()

    def _infer_branch_id(self, record):
        """Best-effort organizational context for F1 org-scoped approval:
        checks common attribute paths so most transaction modules get
        branch-scoped approval eligibility for free, with no code changes
        of their own. Returns None if none of these paths apply — the
        engine then falls back to role-only eligibility (unchanged
        behavior) for that instance."""
        for path in ("vehicle.branch_id", "branch_id", "department.branch_id"):
            obj = record
            try:
                for attr in path.split("."):
                    obj = getattr(obj, attr)
                if obj is not None:
                    return obj
            except AttributeError:
                continue
        return None

    def submit(self, record_id: int, user):
        """Submit a DRAFT record through the Approval Engine."""
        record = db.session.get(self.model, record_id)
        if user is not None and not self._visible_to(record, user):
            raise NotVisibleError(
                "You do not have access to this record.")
        instance = self.engine.submit(
            self.document_type_code, self.reference_table, record_id,
            amount=getattr(record, "amount", None), user=user,
            branch_id=self._infer_branch_id(record),
            document_number=getattr(record, "document_number", None))
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
        if user is not None and not self._visible_to(record, user):
            raise NotVisibleError(
                "You do not have access to this record.")
        self.engine.resubmit(record.approval_instance, user, remarks)
        db.session.commit()
        return record

    def cancel(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        if user is not None and not self._visible_to(record, user):
            raise NotVisibleError(
                "You do not have access to this record.")
        if record.approval_instance_id:
            self.engine.cancel(record.approval_instance, user, remarks)
        record.status = "CANCELLED"
        db.session.commit()
        return record

    def _visible_to(self, record, user) -> bool:
        """Does `user` have visibility into this record?
        - No user passed → no filtering (backward compatible).
        - The record's own requester always sees it, regardless of scope.
        - No org context on the record, or the user has no scope rows
          assigned (not yet opted into org-scoping) → visible (same
          rollout-safety rule as F1's approval eligibility).
        - Otherwise, visible only if the user's UserOrgScope covers the
          record's inferred branch."""
        if user is None:
            return True
        if getattr(record, "requested_by", None) == getattr(user, "id", None):
            return True
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        branch_id = self._infer_branch_id(record)
        return UserOrgScopeService().covers(user.id, branch_id=branch_id)

    def list(self, include_inactive: bool = True, user=None):
        query = db.session.query(self.model)
        if not include_inactive:
            query = query.filter(self.model.is_active.is_(True))
        records = query.order_by(self.model.id.desc()).all()
        if user is None:
            return records
        return [r for r in records if self._visible_to(r, user)]

    def get_visible(self, record_id: int, user):
        """Like get(), but returns None if `user` doesn't have visibility
        into this record per organizational scope — used by detail/edit
        routes so direct-URL access respects the same scoping as list()."""
        record = db.session.get(self.model, record_id)
        if record is None or not self._visible_to(record, user):
            return None
        return record

    def get(self, record_id: int):
        return db.session.get(self.model, record_id)
