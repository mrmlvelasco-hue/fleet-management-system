import pytest

from app.core.search.searchable_service import SearchableService
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


class _VehicleSearchServiceForTest(SearchableService):
    model = Vehicle
    search_fields = ["plate_number", "conduction_number", "brand", "model"]

    def label(self, obj):
        return obj.conduction_number or obj.plate_number


@pytest.fixture()
def env(db):
    manila = BranchService().create(code="BR-SEARCHSCOPE-MNL", name="Manila SearchScope")
    cebu = BranchService().create(code="BR-SEARCHSCOPE-CEB", name="Cebu SearchScope")
    vt = VehicleTypeService().create(code="LV-SEARCHSCOPE", name="Light", category="LIGHT")

    manila_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=manila.id, conduction_number="SEARCHSCOPE-MNL-1")
    cebu_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=cebu.id, conduction_number="SEARCHSCOPE-CEB-1")

    manila_user = User(username="searchscope_manila", email="searchscope_manila@x.com",
                       password_hash="x")
    from app.extensions import db as _db
    _db.session.add(manila_user)
    _db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                branch_id=manila.id)

    return manila, cebu, manila_vehicle, cebu_vehicle, manila_user


def test_search_respects_org_scope_when_user_passed(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search(user=manila_user)
    ids = {v.id for v in items}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id not in ids


def test_search_unrestricted_without_user(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search()
    ids = {v.id for v in items}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id in ids


def test_to_select2_response_respects_org_scope(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    svc = _VehicleSearchServiceForTest()
    resp = svc.to_select2_response(user=manila_user)
    ids = {r["id"] for r in resp["results"]}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id not in ids


def test_to_table_response_respects_org_scope(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    svc = _VehicleSearchServiceForTest()
    resp = svc.to_table_response(user=manila_user)
    ids = {r["id"] for r in resp["rows"]}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id not in ids


def test_unscoped_user_sees_everything(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    unscoped = User(username="searchscope_unscoped", email="searchscope_unscoped@x.com",
                    password_hash="x")
    from app.extensions import db as _db
    _db.session.add(unscoped)
    _db.session.commit()
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search(user=unscoped)
    ids = {v.id for v in items}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id in ids


@pytest.fixture()
def company_env(db):
    """Two Companies, each with a Company-only (branchless) vehicle --
    the exact shape Batch 1-3 made possible and that _apply_org_scope's
    NULL-branch_id-is-visible-to-everyone fallback leaks (2026-10-30)."""
    from app.extensions import db as _db
    from app.modules.master_data.org.service import (
        RegionService, AreaDistrictService, OrgCompanyService)

    region = RegionService().create(code="NL-SS", name="North Luzon SS")
    area = AreaDistrictService().create(code="NL2-SS", name="North Luzon 2 SS",
                                        region_id=region.id)
    co_a = OrgCompanyService().create(short_name="CoA-SS", region_id=region.id,
                                      area_district_id=area.id)
    co_b = OrgCompanyService().create(short_name="CoB-SS", region_id=region.id,
                                      area_district_id=area.id)
    mapped_branch = BranchService().create(code="BR-SS-MAPPED", name="Mapped SS",
                                           company_id=co_a.id)
    vt = VehicleTypeService().create(code="LV-SS", name="Light SS", category="LIGHT")

    co_a_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Wigo", year=2024,
        company_id=co_a.id, conduction_number="SS-COA-1")
    co_b_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        company_id=co_b.id, conduction_number="SS-COB-1")
    co_a_branch_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="Brio", year=2024,
        branch_id=mapped_branch.id, conduction_number="SS-COA-BR-1")

    co_a_user = User(username="searchscope_coa", email="searchscope_coa@x.com",
                     password_hash="x")
    _db.session.add(co_a_user)
    _db.session.commit()
    UserOrgScopeService().assign(co_a_user.id, scope_type="ORG_COMPANY",
                                 company_id=co_a.id)

    return co_a, co_b, co_a_vehicle, co_b_vehicle, co_a_branch_vehicle, co_a_user


def test_org_company_scoped_user_sees_their_own_companys_branchless_vehicle(
        db, company_env):
    co_a, co_b, co_a_vehicle, co_b_vehicle, co_a_branch_vehicle, co_a_user = company_env
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search(user=co_a_user)
    ids = {v.id for v in items}
    assert co_a_vehicle.id in ids


def test_org_company_scoped_user_does_not_see_another_companys_branchless_vehicle(
        db, company_env):
    co_a, co_b, co_a_vehicle, co_b_vehicle, co_a_branch_vehicle, co_a_user = company_env
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search(user=co_a_user)
    ids = {v.id for v in items}
    assert co_b_vehicle.id not in ids


def test_org_company_scoped_user_sees_their_companys_branch_anchored_vehicle(
        db, company_env):
    co_a, co_b, co_a_vehicle, co_b_vehicle, co_a_branch_vehicle, co_a_user = company_env
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search(user=co_a_user)
    ids = {v.id for v in items}
    assert co_a_branch_vehicle.id in ids


def test_branch_scoped_user_does_not_see_another_companys_branchless_vehicle(
        db, company_env):
    """The exact live-bug scenario found in the Batch 4 audit: a plain
    BRANCH-scoped user must not see Company-only vehicles leaking in
    through the old 'branch_id IS NULL -> visible to everyone' rule."""
    co_a, co_b, co_a_vehicle, co_b_vehicle, co_a_branch_vehicle, co_a_user = company_env
    manila = BranchService().create(code="BR-SS-MNL2", name="Manila SS2")
    manila_user = User(username="searchscope_mnl2", email="searchscope_mnl2@x.com",
                       password_hash="x")
    from app.extensions import db as _db
    _db.session.add(manila_user)
    _db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                 branch_id=manila.id)

    svc = _VehicleSearchServiceForTest()
    items, total = svc.search(user=manila_user)
    ids = {v.id for v in items}
    assert co_a_vehicle.id not in ids
    assert co_b_vehicle.id not in ids
