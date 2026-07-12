import pytest

from app.core.approval.engine import ApprovalEngine
from app.core.approval.task_service import ApprovalTaskService
from app.core.approval.models import ApprovalTask
from app.modules.user_management.models import User, Role
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    low_role = Role(name="Task Supervisor")
    high_role = Role(name="Task Manager")
    low_user = User(username="task_sup", email="task_sup@x.com", password_hash="x")
    low_user.roles.append(low_role)
    high_user = User(username="task_mgr", email="task_mgr@x.com", password_hash="x")
    high_user.roles.append(high_role)
    requester = User(username="task_req", email="task_req@x.com", password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([low_role, high_role, low_user, high_user, requester])
    _db.session.commit()

    dt = DocumentTypeService().create(code="TASKT", name="Task Test Doc",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TASKT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="Task Test Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": low_role.id},
        {"level_number": 2, "approver_type": "ROLE", "role_id": high_role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)
    return low_role, high_role, low_user, high_user, requester


def test_submit_creates_pending_task_for_level_1(db, env):
    low_role, high_role, low_user, high_user, requester = env
    engine = ApprovalEngine()
    instance = engine.submit("TASKT", "some_table", 1, user=requester,
                             document_number="TASKT-000001")
    tasks = ApprovalTask.query.filter_by(approval_instance_id=instance.id).all()
    assert len(tasks) == 1
    assert tasks[0].status == "PENDING"
    assert tasks[0].level_number == 1
    assert tasks[0].assigned_role_id == low_role.id
    assert tasks[0].document_number == "TASKT-000001"


def test_approve_completes_current_task_and_creates_next(db, env):
    low_role, high_role, low_user, high_user, requester = env
    engine = ApprovalEngine()
    instance = engine.submit("TASKT", "some_table", 2, user=requester)
    engine.approve(instance, user=low_user)

    tasks = (ApprovalTask.query.filter_by(approval_instance_id=instance.id)
            .order_by(ApprovalTask.level_number).all())
    assert len(tasks) == 2
    assert tasks[0].status == "COMPLETED"
    assert tasks[0].completed_by == low_user.id
    assert tasks[1].status == "PENDING"
    assert tasks[1].level_number == 2
    assert tasks[1].assigned_role_id == high_role.id


def test_final_approval_completes_task_with_no_new_one(db, env):
    low_role, high_role, low_user, high_user, requester = env
    engine = ApprovalEngine()
    instance = engine.submit("TASKT", "some_table", 3, user=requester)
    engine.approve(instance, user=low_user)
    engine.approve(instance, user=high_user)

    tasks = ApprovalTask.query.filter_by(approval_instance_id=instance.id).all()
    assert len(tasks) == 2
    assert all(t.status == "COMPLETED" for t in tasks)


def test_reject_completes_task_and_cancels_none_pending(db, env):
    low_role, high_role, low_user, high_user, requester = env
    engine = ApprovalEngine()
    instance = engine.submit("TASKT", "some_table", 4, user=requester)
    engine.reject(instance, user=low_user, remarks="no good")

    tasks = ApprovalTask.query.filter_by(approval_instance_id=instance.id).all()
    assert len(tasks) == 1
    assert tasks[0].status == "COMPLETED"


def test_list_for_user_shows_pending_task_for_role_holder(db, env):
    low_role, high_role, low_user, high_user, requester = env
    engine = ApprovalEngine()
    engine.submit("TASKT", "some_table", 5, user=requester)

    my_tasks = ApprovalTaskService().list_for_user(low_user)
    assert len(my_tasks) == 1
    assert my_tasks[0].assigned_role_id == low_role.id

    other_tasks = ApprovalTaskService().list_for_user(high_user)
    assert len(other_tasks) == 0  # not yet at level 2


def test_list_for_user_respects_org_scope(db, env):
    from app.modules.user_management.org_scope_service import UserOrgScopeService
    from app.modules.master_data.org.service import BranchService
    low_role, high_role, low_user, high_user, requester = env
    branch_a = BranchService().create(code="BR-TASK-A", name="Task Branch A")
    branch_b = BranchService().create(code="BR-TASK-B", name="Task Branch B")
    UserOrgScopeService().assign(low_user.id, scope_type="BRANCH", branch_id=branch_a.id)

    engine = ApprovalEngine()
    engine.submit("TASKT", "some_table", 6, user=requester, branch_id=branch_b.id)

    my_tasks = ApprovalTaskService().list_for_user(low_user)
    assert len(my_tasks) == 0  # low_user is scoped to branch A, this is branch B


def test_cancel_completes_the_pending_task(db, env):
    low_role, high_role, low_user, high_user, requester = env
    engine = ApprovalEngine()
    instance = engine.submit("TASKT", "some_table", 7, user=requester)
    engine.cancel(instance, user=requester)

    tasks = ApprovalTask.query.filter_by(approval_instance_id=instance.id).all()
    assert len(tasks) == 1
    assert tasks[0].status == "CANCELLED"
