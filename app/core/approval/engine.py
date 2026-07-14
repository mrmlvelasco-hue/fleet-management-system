"""Generic Approval Workflow Engine (runtime).

Any module submits a document with a document type code and a reference to
its own record; the engine resolves the path via the Approval Matrix
(Document Type → Amount Range → Matrix → Path → Levels), walks the levels,
validates approver eligibility, records every action, and emits events that
the Notification Engine (Phase 1c) subscribes to. No approval logic ever
lives inside business modules.
"""
from app.extensions import db
from app.core.approval.models import ApprovalInstance, ApprovalAction
from app.core.approval.task_service import ApprovalTaskService
from app.modules.approval_config.service import ApprovalMatrixService
from app.modules.document_config.repository import DocumentTypeRepository
from app.modules.user_management.org_scope_service import UserOrgScopeService


class NotEligibleApproverError(Exception):
    pass


class InvalidStateError(Exception):
    pass


# Module-level event subscribers: callables (event_name, instance) -> None.
_subscribers: list = []


class ApprovalEngine:
    """Events emitted: submitted, approved_level, approved_final, rejected,
    returned, resubmitted, cancelled."""

    def __init__(self):
        self.doc_types = DocumentTypeRepository()
        self.matrices = ApprovalMatrixService()
        self.tasks = ApprovalTaskService()

    # ---------- events ----------

    def on_event(self, callback) -> None:
        _subscribers.append(callback)

    def _emit(self, name: str, instance: ApprovalInstance) -> None:
        for cb in _subscribers:
            cb(name, instance)

    # ---------- helpers ----------

    def _current_level_def(self, instance: ApprovalInstance):
        for level in instance.approval_path.levels:
            if level.level_number == instance.current_level:
                return level
        raise InvalidStateError("Current level not found on path.")

    def _check_eligible(self, instance: ApprovalInstance, user) -> None:
        level = self._current_level_def(instance)
        if level.approver_type == "USER":
            if level.user_id != user.id:
                raise NotEligibleApproverError(
                    "You are not the designated approver for this level.")
        else:  # ROLE
            if not any(r.id == level.role_id and r.is_active
                       for r in user.roles):
                raise NotEligibleApproverError(
                    "You do not hold the approver role for this level.")
            # F1: Role alone is never sufficient — the user's organizational
            # scope must also cover this transaction's branch/business
            # unit, unless the instance has no recorded org context (full
            # backward compatibility for modules that don't pass one).
            if not UserOrgScopeService().covers(
                    user.id, branch_id=instance.branch_id,
                    business_unit_id=instance.business_unit_id):
                raise NotEligibleApproverError(
                    "You hold the approver role, but your organizational "
                    "scope does not cover this transaction's branch/"
                    "business unit.")

    def _approver_label(self, level) -> str:
        if level.approver_type == "USER":
            return f"User: {level.user.full_name}" if level.user else "User: (unassigned)"
        return f"Role: {level.role.name}" if level.role else "Role: (unassigned)"

    def is_eligible_approver(self, instance, user) -> bool:
        """Non-raising, read-only check — safe to call at display time to
        decide whether to show Approve/Reject/Return buttons at all,
        rather than showing them to everyone and only failing on click."""
        if instance is None or user is None:
            return False
        if instance.status != "PENDING" or not instance.approval_path:
            return False
        try:
            self._check_eligible(instance, user)
            return True
        except (NotEligibleApproverError, InvalidStateError):
            return False

    def get_approval_chain(self, instance) -> list:
        """Every level of the resolved path, in order, with its current
        status (APPROVED/REJECTED/RETURNED/CURRENT/WAITING) and who acted
        on it — the full "approval line" showing who has approved and who
        is next, for display on any transaction's detail page."""
        if instance is None or not instance.approval_path:
            return []
        from app.modules.user_management.models import User

        actions_by_level = {}
        for a in instance.actions:
            if a.action in ("APPROVE", "REJECT", "RETURN"):
                actions_by_level[a.level_number] = a

        status_labels = {"APPROVE": "APPROVED", "REJECT": "REJECTED",
                         "RETURN": "RETURNED"}
        chain = []
        for level in instance.approval_path.levels:
            entry = {
                "level_number": level.level_number,
                "approver_label": self._approver_label(level),
                "status": "WAITING",
                "acted_by_name": None,
                "acted_at": None,
                "remarks": None,
            }
            action = actions_by_level.get(level.level_number)
            if action:
                entry["status"] = status_labels.get(action.action, action.action)
                actor = db.session.get(User, action.acted_by)
                entry["acted_by_name"] = actor.full_name if actor else None
                entry["acted_at"] = action.acted_at
                entry["remarks"] = action.remarks
            elif (level.level_number == instance.current_level
                 and instance.status == "PENDING"):
                entry["status"] = "CURRENT"
            chain.append(entry)
        return chain

    def _record(self, instance, action, user, remarks=None) -> None:
        db.session.add(ApprovalAction(
            instance_id=instance.id, level_number=instance.current_level,
            action=action, acted_by=user.id, remarks=remarks))
        db.session.flush()

    def _require_status(self, instance, *allowed) -> None:
        if instance.status not in allowed:
            raise InvalidStateError(
                f"Action not allowed while status is {instance.status}.")

    # ---------- actions ----------

    def submit(self, document_type_code, reference_table, reference_id,
               amount=None, user=None, branch_id=None,
               business_unit_id=None, document_number=None) -> ApprovalInstance:
        dt = self.doc_types.get_by_code(document_type_code)
        if dt is None:
            raise InvalidStateError(
                f"Unknown document type '{document_type_code}'.")

        instance = ApprovalInstance(
            document_type_id=dt.id, reference_table=reference_table,
            reference_id=reference_id, amount=amount,
            branch_id=branch_id, business_unit_id=business_unit_id,
            submitted_by=user.id if user else None)
        db.session.add(instance)
        db.session.flush()

        if not dt.requires_approval:
            instance.status = "APPROVED"
            instance.current_level = 0
            self._record(instance, "SUBMIT", user)
            db.session.commit()
            self._emit("approved_final", instance)
            return instance

        matrix = self.matrices.resolve(dt.id, amount=amount)
        instance.approval_path_id = matrix.approval_path_id
        instance.status = "PENDING"
        instance.current_level = 1
        self._record(instance, "SUBMIT", user)
        db.session.commit()
        level_1 = self._current_level_def(instance)
        self.tasks.create_for_level(instance, level_1,
                                    document_number=document_number,
                                    requested_by=user.id if user else None)
        db.session.commit()
        self._emit("submitted", instance)
        return instance

    def approve(self, instance, user, remarks=None) -> ApprovalInstance:
        self._require_status(instance, "PENDING")
        self._check_eligible(instance, user)
        self._record(instance, "APPROVE", user, remarks)
        self.tasks.complete_current(instance, user)
        max_level = max(l.level_number for l in instance.approval_path.levels)
        if instance.current_level >= max_level:
            instance.status = "APPROVED"
            db.session.commit()
            self._emit("approved_final", instance)
        else:
            instance.current_level += 1
            db.session.commit()
            next_level = self._current_level_def(instance)
            self.tasks.create_for_level(
                instance, next_level, requested_by=instance.submitted_by)
            db.session.commit()
            self._emit("approved_level", instance)
        return instance

    def reject(self, instance, user, remarks=None) -> ApprovalInstance:
        self._require_status(instance, "PENDING")
        self._check_eligible(instance, user)
        self._record(instance, "REJECT", user, remarks)
        self.tasks.complete_current(instance, user)
        instance.status = "REJECTED"
        db.session.commit()
        self._emit("rejected", instance)
        return instance

    def return_document(self, instance, user, remarks=None) -> ApprovalInstance:
        self._require_status(instance, "PENDING")
        self._check_eligible(instance, user)
        self._record(instance, "RETURN", user, remarks)
        self.tasks.complete_current(instance, user)
        instance.status = "RETURNED"
        instance.current_level = 0
        db.session.commit()
        self._emit("returned", instance)
        return instance

    def resubmit(self, instance, user, remarks=None) -> ApprovalInstance:
        self._require_status(instance, "RETURNED")
        instance.status = "PENDING"
        instance.current_level = 1
        self._record(instance, "SUBMIT", user, remarks)
        db.session.commit()
        level_1 = self._current_level_def(instance)
        self.tasks.create_for_level(instance, level_1,
                                    requested_by=instance.submitted_by)
        db.session.commit()
        self._emit("resubmitted", instance)
        return instance

    def cancel(self, instance, user, remarks=None) -> ApprovalInstance:
        self._require_status(instance, "DRAFT", "PENDING", "RETURNED")
        self._record(instance, "CANCEL", user, remarks)
        self.tasks.complete_current(instance, user, outcome_status="CANCELLED")
        self.tasks.cancel_remaining(instance)
        instance.status = "CANCELLED"
        db.session.commit()
        self._emit("cancelled", instance)
        return instance
