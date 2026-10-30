"""A tire/battery with no Branch must not become visible to everyone
(2026-10-30, Batch 3).

Same trap as Vehicle and Driver (see test_nullable_branch_visibility.py):
tires.branch_id / batteries.branch_id were already nullable before this
batch, and TireService/BatteryService's list()/get_visible() passed
only branch_id into UserOrgScopeService.covers() -- which treats "no
org context" as "no restriction." A Company-only tire or battery would
therefore pass every scope check and be visible to EVERY user,
regardless of scope, until company_id is passed alongside branch_id.
"""
import pytest

from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.org.service import (
    RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.tire.service import TireService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.core.security.password import hash_password


@pytest.fixture()
def env(db):
    north = RegionService().create(code="NL-TBV", name="North Luzon")
    south = RegionService().create(code="SL-TBV", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-TBV", name="NORTH LUZON 2",
                                       region_id=north.id)
    cav = AreaDistrictService().create(code="CAV-TBV", name="CAVITE 2",
                                       region_id=south.id)
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    cav_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=cav.id)
    scoped = User(username="scopedtbv", email="scopedtbv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    db.session.add(scoped)
    db.session.commit()
    UserOrgScopeService().assign(scoped.id, scope_type="ORG_COMPANY",
                                 company_id=nl2_co.id)
    return {"nl2_co": nl2_co, "cav_co": cav_co, "scoped": scoped}


def test_a_branchless_tire_is_hidden_from_another_companys_scope(db, env):
    TireService().create(
        serial_number="T-TBV-SOUTH", brand="Bridgestone", size="185/65R15",
        tire_type="RADIAL", company_id=env["cav_co"].id)
    visible = TireService().list(user=env["scoped"])
    serials = {t.serial_number for t in visible}
    assert "T-TBV-SOUTH" not in serials, (
        "a tire with no branch leaked past an ORG_COMPANY scope")


def test_a_branchless_tire_IS_visible_to_its_own_companys_scope(db, env):
    TireService().create(
        serial_number="T-TBV-NORTH", brand="Bridgestone", size="185/65R15",
        tire_type="RADIAL", company_id=env["nl2_co"].id)
    visible = TireService().list(user=env["scoped"])
    assert "T-TBV-NORTH" in {t.serial_number for t in visible}


def test_a_branchless_battery_is_hidden_from_another_companys_scope(
        db, env):
    BatteryService().create(
        serial_number="B-TBV-SOUTH", brand="Motolite",
        company_id=env["cav_co"].id)
    visible = BatteryService().list(user=env["scoped"])
    serials = {b.serial_number for b in visible}
    assert "B-TBV-SOUTH" not in serials, (
        "a battery with no branch leaked past an ORG_COMPANY scope")


def test_a_branchless_battery_IS_visible_to_its_own_companys_scope(
        db, env):
    BatteryService().create(
        serial_number="B-TBV-NORTH", brand="Motolite",
        company_id=env["nl2_co"].id)
    visible = BatteryService().list(user=env["scoped"])
    assert "B-TBV-NORTH" in {b.serial_number for b in visible}
