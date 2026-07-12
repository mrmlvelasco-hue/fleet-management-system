"""Generic Approval Task / Inbox service (F2). Fully managed by the
ApprovalEngine — creates/completes/cancels tasks as instances move through
their approval levels. `list_for_user()` is the single reusable query
behind the "For My Action" worklist: any approval-enabled module
participates automatically, with no per-module pending-approval query."""
from datetime import datetime, timezone

from app.extensions import db
from app.core.approval.models import ApprovalTask
from app.modules.user_management.org_scope_service import UserOrgScopeService


class ApprovalTaskService:
    def create_for_level(self, instance, level, *, document_number=None,
                         requested_by=None) -> ApprovalTask:
        task = ApprovalTask(
            approval_instance_id=instance.id, level_number=level.level_number,
            document_type_id=instance.document_type_id,
            document_number=document_number,
            reference_table=instance.reference_table,
            reference_id=instance.reference_id,
            assigned_role_id=level.role_id if level.approver_type == "ROLE" else None,
            assigned_user_id=level.user_id if level.approver_type == "USER" else None,
            branch_id=instance.branch_id,
            business_unit_id=instance.business_unit_id,
            status="PENDING", requested_by=requested_by)
        db.session.add(task)
        db.session.flush()
        return task

    def complete_current(self, instance, user, outcome_status="COMPLETED") -> None:
        task = (ApprovalTask.query
               .filter_by(approval_instance_id=instance.id,
                         level_number=instance.current_level, status="PENDING")
               .first())
        if task:
            task.status = outcome_status
            task.completed_at = datetime.now(timezone.utc)
            task.completed_by = user.id if user else None
            db.session.flush()

    def cancel_remaining(self, instance) -> None:
        (ApprovalTask.query
         .filter_by(approval_instance_id=instance.id, status="PENDING")
         .update({"status": "CANCELLED"}))
        db.session.flush()

    def list_for_user(self, user) -> list:
        """PENDING tasks this user can act on: directly assigned (USER-type
        level), or holding the assigned role AND their org scope covers
        the task's branch/business unit."""
        role_ids = [r.id for r in user.roles if r.is_active]
        scope_svc = UserOrgScopeService()

        candidates = (ApprovalTask.query
                     .filter_by(status="PENDING")
                     .filter(db.or_(
                         ApprovalTask.assigned_user_id == user.id,
                         ApprovalTask.assigned_role_id.in_(role_ids) if role_ids
                         else db.false()))
                     .order_by(ApprovalTask.created_at)
                     .all())

        return [t for t in candidates
               if t.assigned_user_id == user.id
               or scope_svc.covers(user.id, branch_id=t.branch_id,
                                   business_unit_id=t.business_unit_id)]
