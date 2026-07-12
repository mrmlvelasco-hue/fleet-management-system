from datetime import date

import pytest

from app.core.approval.engine import ApprovalEngine, NotEligibleApproverError
from app.modules.user_management.models import User, Role
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.modules.master_data.org.service import BranchService
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch_a = BranchService().create(code="BR-ORG-A", name="Manila")
    branch_b = BranchService().create(code="BR-ORG-B", name="Cebu")

    role = Role(name="Fleet Manager Org")
    manila_manager = User(username="juan_org", email="juan_org@x.com",
                          password_hash="x")
    manila_manager.roles.append(role)
    cebu_manager = User(username="pedro_org", email="pedro_org@x.com",
                        password_hash="x")
    cebu_manager.roles.append(role)

    from app.extensions import db as _db
    _db.session.add_all([role, manila_manager, cebu_manager])
    _db.session.commit()

    scope_svc = UserOrgScopeService()
    scope_svc.assign(manila_manager.id, scope_type="BRANCH", branch_id=branch_a.id)
    scope_svc.assign(cebu_manager.id, scope_type="BRANCH", branch_id=branch_b.id)

    dt = DocumentTypeService().create(code="ORGT", name="Org Scope Test Doc",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="ORGT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="Org Scope Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    return branch_a, branch_b, role, manila_manager, cebu_manager


def test_manila_manager_can_approve_manila_transaction(db, env):
    branch_a, branch_b, role, manila_manager, cebu_manager = env
    engine = ApprovalEngine()
    instance = engine.submit("ORGT", "some_table", 1, user=manila_manager,
                             branch_id=branch_a.id)
    engine.approve(instance, user=manila_manager)
    assert instance.status == "APPROVED"


def test_cebu_manager_cannot_approve_manila_transaction(db, env):
    branch_a, branch_b, role, manila_manager, cebu_manager = env
    engine = ApprovalEngine()
    instance = engine.submit("ORGT", "some_table", 2, user=manila_manager,
                             branch_id=branch_a.id)
    with pytest.raises(NotEligibleApproverError):
        engine.approve(instance, user=cebu_manager)


def test_global_scope_can_approve_any_branch(db, env):
    branch_a, branch_b, role, manila_manager, cebu_manager = env
    global_user = User(username="maria_global", email="maria_global@x.com",
                       password_hash="x")
    global_user.roles.append(role)
    from app.extensions import db as _db
    _db.session.add(global_user)
    _db.session.commit()
    UserOrgScopeService().assign(global_user.id, scope_type="GLOBAL")

    engine = ApprovalEngine()
    instance = engine.submit("ORGT", "some_table", 3, user=manila_manager,
                             branch_id=branch_a.id)
    engine.approve(instance, user=global_user)
    assert instance.status == "APPROVED"


def test_no_branch_context_falls_back_to_role_only(db, env):
    """Backward compatibility: an instance submitted without branch_id
    (e.g. an older/simpler module) should resolve by role membership
    alone, same as before F1."""
    branch_a, branch_b, role, manila_manager, cebu_manager = env
    engine = ApprovalEngine()
    instance = engine.submit("ORGT", "some_table", 4, user=manila_manager)
    # No branch_id passed — Cebu manager should still be able to approve
    # since there's no org context to restrict against.
    engine.approve(instance, user=cebu_manager)
    assert instance.status == "APPROVED"


def test_user_type_level_unaffected_by_org_scope(db, env):
    """USER-type levels (specific designated approver) should ignore org
    scope entirely — they're already fully specific."""
    branch_a, branch_b, role, manila_manager, cebu_manager = env
    path = ApprovalPathService().create(name="User Level Org Path", levels=[
        {"level_number": 1, "approver_type": "USER", "user_id": cebu_manager.id}])
    dt2 = DocumentTypeService().create(code="ORGT2", name="Org Test 2",
                                       requires_approval=True,
                                       auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt2.id, prefix="ORGT2",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    ApprovalMatrixService().create(dt2.id, path.id, min_amount=None, max_amount=None)

    engine = ApprovalEngine()
    instance = engine.submit("ORGT2", "some_table", 5, user=manila_manager,
                             branch_id=branch_a.id)
    # cebu_manager has no scope over branch_a, but is the designated USER
    # approver, so this should succeed regardless.
    engine.approve(instance, user=cebu_manager)
    assert instance.status == "APPROVED"
