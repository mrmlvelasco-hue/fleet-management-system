"""A vehicle with no Branch must not become visible to everyone.

Michael chose (2026-10-30): remove Branch from the Vehicle and Driver
forms, make Company the required field in its place. That means
vehicles.branch_id and drivers.branch_id become NULLABLE.

The trap: UserOrgScopeService.covers() short-circuits to True when no
org context is passed at all (backward compatibility for records that
predate org scoping). So a record with branch_id = NULL would pass every
scope check and become visible to EVERY user regardless of their scope
-- a silent widening of access, introduced by what looks like a form
change.

Requiring Company is what prevents it: every new record still has an org
anchor, and the visibility checks pass company_id alongside branch_id so
a null branch resolves through Company instead of falling through.

These tests exist because that failure would be invisible in Michael's
current data -- his users are all global today, so nothing would look
wrong until the first scoped user is created.
"""
import pytest

from app.extensions import db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.core.security.password import hash_password


@pytest.fixture()
def env(db):
    north = RegionService().create(code="NL-NV", name="North Luzon")
    south = RegionService().create(code="SL-NV", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-NV", name="NORTH LUZON 2",
                                       region_id=north.id)
    cav = AreaDistrictService().create(code="CAV-NV", name="CAVITE 2",
                                       region_id=south.id)
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    cav_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=cav.id)
    vt = VehicleType(code="CAR-NV", name="Car", category="LIGHT")
    db.session.add(vt)
    db.session.commit()

    # Scoped to the NORTH company only.
    scoped = User(username="scopednv", email="scopednv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    db.session.add(scoped)
    db.session.commit()
    UserOrgScopeService().assign(scoped.id, scope_type="ORG_COMPANY",
                                 company_id=nl2_co.id)
    return {"nl2_co": nl2_co, "cav_co": cav_co, "vt": vt, "scoped": scoped}


def test_branch_is_nullable_on_vehicle_and_driver(db, env):
    """The schema change the form change depends on."""
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.master_data.driver.models import Driver
    assert Vehicle.__table__.columns["branch_id"].nullable is True
    assert Driver.__table__.columns["branch_id"].nullable is True


def test_a_vehicle_can_be_created_with_a_company_and_no_branch(db, env):
    """What the new form posts."""
    v = VehicleService().create(
        vehicle_type_id=env["vt"].id, brand="Toyota", model="Hilux",
        year=2024, plate_number="NV-001", company_id=env["nl2_co"].id)
    assert v.branch_id is None
    assert v.company_id == env["nl2_co"].id


def test_a_branchless_vehicle_is_hidden_from_another_companys_scope(
        db, env):
    """THE regression this file exists for. Without company_id in the
    visibility check, covers() short-circuits on the null branch and
    this vehicle appears for everyone."""
    VehicleService().create(
        vehicle_type_id=env["vt"].id, brand="Toyota", model="Hilux",
        year=2024, plate_number="NV-SOUTH", company_id=env["cav_co"].id)
    visible = VehicleService().list(user=env["scoped"])
    plates = {v.plate_number for v in visible}
    assert "NV-SOUTH" not in plates, (
        "a vehicle with no branch leaked past an ORG_COMPANY scope")


def test_a_branchless_vehicle_IS_visible_to_its_own_companys_scope(
        db, env):
    """The other half: scoping must not hide what the user should see."""
    VehicleService().create(
        vehicle_type_id=env["vt"].id, brand="Toyota", model="Hilux",
        year=2024, plate_number="NV-NORTH", company_id=env["nl2_co"].id)
    visible = VehicleService().list(user=env["scoped"])
    assert "NV-NORTH" in {v.plate_number for v in visible}


def test_the_paged_list_agrees_with_the_unpaged_one(db, env):
    """list() and list_page() must return the same set -- the dashboard
    counts through one and the screen pages through the other."""
    for plate, co in (("NV-A", env["nl2_co"]), ("NV-B", env["cav_co"])):
        VehicleService().create(
            vehicle_type_id=env["vt"].id, brand="Toyota", model="Hilux",
            year=2024, plate_number=plate, company_id=co.id)
    unpaged = {v.plate_number for v in VehicleService().list(
        user=env["scoped"])}
    rows, _total = VehicleService().list_page(user=env["scoped"],
                                              page=1, page_size=100)
    assert {v.plate_number for v in rows} == unpaged
