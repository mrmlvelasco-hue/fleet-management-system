from datetime import date

import pytest

from app.core.approval.engine import ApprovalEngine, NotEligibleApproverError
from app.modules.user_management.models import User, Role
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
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


# ── Stage 3B: company_id snapshot + ORG_COMPANY/ORG_REGION eligibility ──

@pytest.fixture()
def company_env(db):
    region_a = RegionService().create(code="RG-APP-A", name="Luzon App")
    region_b = RegionService().create(code="RG-APP-B", name="Visayas App")
    area_a = AreaDistrictService().create(code="AD-APP-A", name="San Mateo App",
                                          region_id=region_a.id)
    area_b = AreaDistrictService().create(code="AD-APP-B", name="Bacolod App",
                                          region_id=region_b.id)
    company_a = OrgCompanyService().create(short_name="Excellence App A",
                                           region_id=region_a.id,
                                           area_district_id=area_a.id)
    company_b = OrgCompanyService().create(short_name="Excellence App B",
                                           region_id=region_b.id,
                                           area_district_id=area_b.id)

    role = Role(name="Fleet Manager Company Org")
    company_a_manager = User(username="juan_company", email="juan_company@x.com",
                             password_hash="x")
    company_a_manager.roles.append(role)
    other_manager = User(username="pedro_company", email="pedro_company@x.com",
                         password_hash="x")
    other_manager.roles.append(role)

    from app.extensions import db as _db
    _db.session.add_all([role, company_a_manager, other_manager])
    _db.session.commit()

    UserOrgScopeService().assign(company_a_manager.id, scope_type="ORG_COMPANY",
                                 company_id=company_a.id)

    dt = DocumentTypeService().create(code="ORGC", name="Org Company Scope Test",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="ORGC",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="Org Company Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    return (region_a, region_b, area_a, area_b, company_a, company_b,
            role, company_a_manager, other_manager)


def test_org_company_scoped_user_can_approve_matching_company(db, company_env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     role, company_a_manager, other_manager) = company_env
    engine = ApprovalEngine()
    instance = engine.submit("ORGC", "some_table", 101, user=other_manager,
                             company_id=company_a.id)
    engine.approve(instance, user=company_a_manager)
    assert instance.status == "APPROVED"


def test_org_company_scoped_user_cannot_approve_a_different_company(
        db, company_env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     role, company_a_manager, other_manager) = company_env
    engine = ApprovalEngine()
    instance = engine.submit("ORGC", "some_table", 102, user=other_manager,
                             company_id=company_b.id)
    with pytest.raises(NotEligibleApproverError):
        engine.approve(instance, user=company_a_manager)


def test_org_region_scoped_user_can_approve_any_company_in_that_region(
        db, company_env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     role, company_a_manager, other_manager) = company_env
    region_manager = User(username="maria_region", email="maria_region@x.com",
                          password_hash="x")
    region_manager.roles.append(role)
    from app.extensions import db as _db
    _db.session.add(region_manager)
    _db.session.commit()
    UserOrgScopeService().assign(region_manager.id, scope_type="ORG_REGION",
                                 region_id=region_a.id)

    engine = ApprovalEngine()
    instance = engine.submit("ORGC", "some_table", 103, user=other_manager,
                             company_id=company_a.id)
    engine.approve(instance, user=region_manager)
    assert instance.status == "APPROVED"


def test_no_company_context_falls_back_to_role_only(db, company_env):
    """Backward compatibility, mirrored from the branch-level version:
    an instance submitted without company_id resolves by role alone."""
    (region_a, region_b, area_a, area_b, company_a, company_b,
     role, company_a_manager, other_manager) = company_env
    engine = ApprovalEngine()
    instance = engine.submit("ORGC", "some_table", 104, user=other_manager)
    # other_manager has no ORG_COMPANY scope at all, but there's no
    # company context recorded on the instance to restrict against.
    engine.approve(instance, user=other_manager)
    assert instance.status == "APPROVED"


def test_the_instance_snapshots_the_company_id_passed_at_submit(db, company_env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     role, company_a_manager, other_manager) = company_env
    engine = ApprovalEngine()
    instance = engine.submit("ORGC", "some_table", 105, user=other_manager,
                             company_id=company_a.id)
    assert instance.company_id == company_a.id
