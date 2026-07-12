import pytest

from app.modules.user_management.org_scope_service import (
    UserOrgScopeService, InvalidScopeError)
from app.modules.user_management.models import User, Role
from app.modules.master_data.org.service import BranchService, BusinessUnitService


@pytest.fixture()
def env(db):
    branch_a = BranchService().create(code="BR-SCOPE-A", name="Branch A")
    branch_b = BranchService().create(code="BR-SCOPE-B", name="Branch B")
    bu = BusinessUnitService().create(code="BU-SCOPE", name="Fleet BU")
    role = Role(name="Fleet Manager Scope Test")
    user_a = User(username="juan_scope", email="juan_scope@x.com", password_hash="x")
    user_a.roles.append(role)
    from app.extensions import db as _db
    _db.session.add_all([role, user_a])
    _db.session.commit()
    return branch_a, branch_b, bu, role, user_a


def test_assign_branch_scope(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    scope = svc.assign(user_a.id, scope_type="BRANCH", branch_id=branch_a.id)
    assert scope.id is not None
    assert scope.branch_id == branch_a.id


def test_assign_global_scope_requires_no_branch(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    scope = svc.assign(user_a.id, scope_type="GLOBAL")
    assert scope.scope_type == "GLOBAL"
    assert scope.branch_id is None


def test_branch_scope_requires_branch_id(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    with pytest.raises(InvalidScopeError):
        svc.assign(user_a.id, scope_type="BRANCH", branch_id=None)


def test_user_can_have_multiple_branch_scopes(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="BRANCH", branch_id=branch_a.id)
    svc.assign(user_a.id, scope_type="BRANCH", branch_id=branch_b.id)
    scopes = svc.list_for_user(user_a.id)
    assert len(scopes) == 2


def test_covers_branch_true_for_matching_branch_scope(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="BRANCH", branch_id=branch_a.id)
    assert svc.covers(user_a.id, branch_id=branch_a.id) is True
    assert svc.covers(user_a.id, branch_id=branch_b.id) is False


def test_covers_branch_true_for_global_scope(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="GLOBAL")
    assert svc.covers(user_a.id, branch_id=branch_a.id) is True
    assert svc.covers(user_a.id, branch_id=branch_b.id) is True


def test_covers_business_unit_scope(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="BUSINESS_UNIT", business_unit_id=bu.id)
    assert svc.covers(user_a.id, business_unit_id=bu.id) is True
    assert svc.covers(user_a.id, branch_id=branch_a.id) is False


def test_covers_returns_true_when_no_context_requested(db, env):
    """No branch/BU passed at all means the caller has no org context to
    check against — always passes (used for backward-compat instances)."""
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    assert svc.covers(user_a.id, branch_id=None, business_unit_id=None) is True


def test_covers_true_when_user_has_no_scopes_at_all(db, env):
    """Design decision: a user with zero UserOrgScope rows hasn't been
    opted into org-scoping yet — treated as unrestricted (legacy/backward
    compatible) rather than restricted-to-nothing, so rolling out F1 never
    silently locks out every existing approver on upgrade."""
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    assert svc.covers(user_a.id, branch_id=branch_a.id) is True


def test_covers_false_once_user_has_a_non_matching_scope(db, env):
    """Once an admin HAS assigned at least one scope, it becomes strict —
    a Branch-A-only user does not also cover Branch B."""
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="BRANCH", branch_id=branch_a.id)
    assert svc.covers(user_a.id, branch_id=branch_b.id) is False


def test_remove_scope(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    scope = svc.assign(user_a.id, scope_type="BRANCH", branch_id=branch_a.id)
    svc.remove(scope.id)
    assert svc.list_for_user(user_a.id) == []
