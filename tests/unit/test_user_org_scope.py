import pytest

from app.modules.user_management.org_scope_service import (
    UserOrgScopeService, InvalidScopeError)
from app.modules.user_management.models import User, Role
from app.modules.master_data.org.service import (
    BranchService, BusinessUnitService, RegionService, AreaDistrictService,
    OrgCompanyService)


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


@pytest.fixture()
def org_env(db):
    """Region > Area/District > Company hierarchy (Stage 3A) used by the
    Stage 3B ORG_COMPANY/ORG_REGION/ORG_AREA_DISTRICT scope tests."""
    region_a = RegionService().create(code="RG-SCOPE-A", name="Luzon Scope")
    region_b = RegionService().create(code="RG-SCOPE-B", name="Visayas Scope")
    area_a = AreaDistrictService().create(code="AD-SCOPE-A", name="San Mateo Scope",
                                          region_id=region_a.id)
    area_b = AreaDistrictService().create(code="AD-SCOPE-B", name="Bacolod Scope",
                                          region_id=region_b.id)
    company_a = OrgCompanyService().create(short_name="Excellence Scope A",
                                           region_id=region_a.id,
                                           area_district_id=area_a.id)
    company_b = OrgCompanyService().create(short_name="Excellence Scope B",
                                           region_id=region_b.id,
                                           area_district_id=area_b.id)
    role = Role(name="Fleet Manager Org Scope Test")
    user_a = User(username="org_scope_user", email="org_scope_user@x.com",
                 password_hash="x")
    user_a.roles.append(role)
    from app.extensions import db as _db
    _db.session.add_all([role, user_a])
    _db.session.commit()
    return region_a, region_b, area_a, area_b, company_a, company_b, user_a


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


def test_allowed_branch_ids_none_when_user_has_no_scopes(db, env):
    """Same backward-compatible default as covers(): no scope rows at
    all means unrestricted, not restricted-to-nothing."""
    branch_a, branch_b, bu, role, user_a = env
    assert UserOrgScopeService().allowed_branch_ids(user_a.id) is None


def test_allowed_branch_ids_none_for_global_or_company_scope(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="GLOBAL")
    assert svc.allowed_branch_ids(user_a.id) is None


def test_allowed_branch_ids_lists_only_the_users_own_branches(db, env):
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="BRANCH", branch_id=branch_a.id)
    assert svc.allowed_branch_ids(user_a.id) == [branch_a.id]


def test_allowed_branch_ids_empty_list_when_only_a_bu_scope_is_held(db, env):
    """A BUSINESS_UNIT-only scope restricts the user but names no
    branch -- this must come back as an empty list (restricted to
    nothing BY BRANCH), never None (which would mean unrestricted)."""
    branch_a, branch_b, bu, role, user_a = env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="BUSINESS_UNIT", business_unit_id=bu.id)
    assert UserOrgScopeService().allowed_branch_ids(user_a.id) == []


# ── Stage 3B: ORG_COMPANY / ORG_REGION / ORG_AREA_DISTRICT ──────────────
# Deliberately distinct names from the legacy "COMPANY" value, which
# already means "unrestricted" (see UserOrgScope's docstring) and must
# keep meaning exactly that.

def test_org_company_scope_requires_company_id(db, org_env):
    *_, user_a = org_env
    svc = UserOrgScopeService()
    with pytest.raises(InvalidScopeError):
        svc.assign(user_a.id, scope_type="ORG_COMPANY", company_id=None)


def test_org_region_scope_requires_region_id(db, org_env):
    *_, user_a = org_env
    svc = UserOrgScopeService()
    with pytest.raises(InvalidScopeError):
        svc.assign(user_a.id, scope_type="ORG_REGION", region_id=None)


def test_org_area_district_scope_requires_area_district_id(db, org_env):
    *_, user_a = org_env
    svc = UserOrgScopeService()
    with pytest.raises(InvalidScopeError):
        svc.assign(user_a.id, scope_type="ORG_AREA_DISTRICT",
                   area_district_id=None)


def test_assign_org_company_scope(db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    scope = svc.assign(user_a.id, scope_type="ORG_COMPANY",
                       company_id=company_a.id)
    assert scope.company_id == company_a.id


def test_covers_org_company_scope_matches_only_that_company(db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_COMPANY", company_id=company_a.id)
    assert svc.covers(user_a.id, company_id=company_a.id) is True
    assert svc.covers(user_a.id, company_id=company_b.id) is False


def test_covers_org_region_scope_matches_every_company_in_that_region(
        db, org_env):
    """A Region scope covers any Company filed under it, even one the
    admin granting the scope never individually named -- the whole
    point of a Region-level grant."""
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_REGION", region_id=region_a.id)
    assert svc.covers(user_a.id, company_id=company_a.id) is True
    assert svc.covers(user_a.id, company_id=company_b.id) is False


def test_covers_org_area_district_scope_matches_companies_in_that_area(
        db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_AREA_DISTRICT",
              area_district_id=area_a.id)
    assert svc.covers(user_a.id, company_id=company_a.id) is True
    assert svc.covers(user_a.id, company_id=company_b.id) is False


def test_covers_true_for_legacy_company_and_global_scope_regardless_of_company(
        db, org_env):
    """The legacy "COMPANY" value and GLOBAL must keep meaning
    unrestricted no matter what company_id is passed -- Stage 3B must
    never narrow their existing behavior."""
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="COMPANY")
    assert svc.covers(user_a.id, company_id=company_a.id) is True
    assert svc.covers(user_a.id, company_id=company_b.id) is True


def test_no_company_context_falls_back_to_true(db, org_env):
    """Same backward-compat rule as branch/BU: no company_id passed at
    all (an older/unrelated instance) means nothing to check -- passes."""
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_COMPANY", company_id=company_a.id)
    assert svc.covers(user_a.id, company_id=None) is True


def test_allowed_branch_ids_empty_when_only_an_org_company_scope_is_held(
        db, org_env):
    """ORG_COMPANY restricts the user but names no branch -- same
    conservative rule as BUSINESS_UNIT-only: empty list, not None."""
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_COMPANY", company_id=company_a.id)
    assert svc.allowed_branch_ids(user_a.id) == []


# ── Batch 7: covers_org() -- a bare Region/Area-District target, for an
# object (a Vendor) assigned directly to one rather than to a specific
# Company in it. covers() cannot express this: it only ever resolves a
# region/area FROM a company_id, so it has no way to ask "does this
# scope cover Region X" when nothing has told it which Company. ────────

def test_covers_org_no_target_falls_back_to_true(db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_COMPANY", company_id=company_a.id)
    assert svc.covers_org(user_a.id) is True


def test_covers_org_true_when_user_has_no_scopes_at_all(db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    assert svc.covers_org(user_a.id, company_id=company_a.id) is True


def test_covers_org_global_and_legacy_company_scope_cover_everything(
        db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="GLOBAL")
    assert svc.covers_org(user_a.id, region_id=region_b.id) is True
    assert svc.covers_org(user_a.id, area_district_id=area_b.id) is True
    assert svc.covers_org(user_a.id, company_id=company_b.id) is True


def test_covers_org_company_target_matches_an_org_company_scope(db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_COMPANY", company_id=company_a.id)
    assert svc.covers_org(user_a.id, company_id=company_a.id) is True
    assert svc.covers_org(user_a.id, company_id=company_b.id) is False


def test_covers_org_region_target_matches_an_org_region_scope_exactly(
        db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_REGION", region_id=region_a.id)
    assert svc.covers_org(user_a.id, region_id=region_a.id) is True
    assert svc.covers_org(user_a.id, region_id=region_b.id) is False


def test_covers_org_region_target_matches_a_narrower_org_company_scope(
        db, org_env):
    """A user scoped to one Company within a Region can see something
    assigned to that whole Region -- their Company is part of it."""
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_COMPANY", company_id=company_a.id)
    assert svc.covers_org(user_a.id, region_id=region_a.id) is True
    assert svc.covers_org(user_a.id, region_id=region_b.id) is False


def test_covers_org_region_target_matches_a_narrower_org_area_district_scope(
        db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_AREA_DISTRICT",
              area_district_id=area_a.id)
    assert svc.covers_org(user_a.id, region_id=region_a.id) is True
    assert svc.covers_org(user_a.id, region_id=region_b.id) is False


def test_covers_org_area_district_target_matches_exactly(db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_AREA_DISTRICT",
              area_district_id=area_a.id)
    assert svc.covers_org(user_a.id, area_district_id=area_a.id) is True
    assert svc.covers_org(user_a.id, area_district_id=area_b.id) is False


def test_covers_org_area_district_target_matches_a_broader_org_region_scope(
        db, org_env):
    """A user scoped to the whole Region covers any Area/District
    inside it, same direction as the Company case above."""
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_REGION", region_id=region_a.id)
    assert svc.covers_org(user_a.id, area_district_id=area_a.id) is True
    assert svc.covers_org(user_a.id, area_district_id=area_b.id) is False


def test_covers_org_company_target_matches_a_broader_region_or_area_scope(
        db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_REGION", region_id=region_a.id)
    assert svc.covers_org(user_a.id, company_id=company_a.id) is True
    assert svc.covers_org(user_a.id, company_id=company_b.id) is False


def test_covers_org_false_once_user_has_a_non_matching_scope(db, org_env):
    region_a, region_b, area_a, area_b, company_a, company_b, user_a = org_env
    svc = UserOrgScopeService()
    svc.assign(user_a.id, scope_type="ORG_COMPANY", company_id=company_b.id)
    assert svc.covers_org(user_a.id, region_id=region_a.id) is False
