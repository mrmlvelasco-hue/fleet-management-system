"""One entry per company in the filters (client decision, 2026-11-16).

Until now every Company dropdown listed one line per Region/Area row
("Excellence (South Luzon / CAVITE 2)"), because per D2 the same company
is stored once per Region + Area/District. Michael's client wants the
dashboard, the mobile app and the list filters to show each company
ONCE, as "Short Name - Company Name", with no Region/Area and no
deactivated company.

Nothing is merged in the data: the area rows stay exactly as they are.
What changes is

  * the label           -> company_display()
  * the dropdown        -> company_options(): one option per company
  * what a filter means -> company_filter(): any one of a company's
                           rows selects ALL of that company's rows

and what must NOT change:

  * a user's ORG_COMPANY access scope stays the single row it was
    granted on (exact_company_filter) -- showing a company once in a
    dropdown must never widen what anybody is allowed to see;
  * the Vehicle form's own picker (/reference/org-companies with no
    flag) and the Vehicle Register report keep Region/Area.
"""
import json

import pytest

from app.extensions import db as _db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def env(db):
    from app.modules.master_data.reference.models import VehicleType
    south = RegionService().create(code="SL-WF", name="South Luzon")
    north = RegionService().create(code="NL-WF", name="North Luzon")
    cav = AreaDistrictService().create(code="CAV-WF", name="CAVITE 2",
                                       region_id=south.id)
    sl3 = AreaDistrictService().create(code="SL3-WF", name="SOUTH LUZON 3",
                                       region_id=south.id)
    nl2 = AreaDistrictService().create(code="NL2-WF", name="NORTH LUZON 2",
                                       region_id=north.id)
    name = "Excellence Poultry & Livestock Specialist Inc"
    exc_cav = OrgCompanyService().create(
        short_name="Excellence", company_name=name,
        region_id=south.id, area_district_id=cav.id)
    exc_sl3 = OrgCompanyService().create(
        short_name="Excellence", company_name=name,
        region_id=south.id, area_district_id=sl3.id)
    exc_nl2 = OrgCompanyService().create(
        short_name="Excellence", company_name=name,
        region_id=north.id, area_district_id=nl2.id)
    eavpi = OrgCompanyService().create(
        short_name="EAVPI", company_name="East Asia Veterinary Products, Inc",
        region_id=south.id, area_district_id=cav.id)
    personal = OrgCompanyService().create(
        short_name="PERSONAL", company_name="PERSONAL",
        region_id=south.id, area_district_id=sl3.id)
    closed = OrgCompanyService().create(
        short_name="CLOSED", company_name="Closed Company Inc",
        region_id=south.id, area_district_id=cav.id)
    OrgCompanyService().deactivate(closed.id)

    sl3_branch = BranchService().create(code="BR-SL3-WF", name="SL3 Branch",
                                        company_id=exc_sl3.id)
    vt = VehicleType(code="CAR-WF", name="Car", category="LIGHT")
    _db.session.add(vt)
    _db.session.commit()

    def _veh(plate, company_id=None, branch_id=None):
        return VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
            branch_id=branch_id, company_id=company_id, plate_number=plate)

    return {
        "exc_cav": exc_cav, "exc_sl3": exc_sl3, "exc_nl2": exc_nl2,
        "eavpi": eavpi, "personal": personal, "closed": closed,
        "v_cav": _veh("WF-CAV", exc_cav.id),
        "v_nl2": _veh("WF-NL2", exc_nl2.id),
        # No company of its own: belongs to Excellence through its branch.
        "v_sl3_branch": _veh("WF-SL3", branch_id=sl3_branch.id),
        "v_eavpi": _veh("WF-EAV", eavpi.id),
    }


# ── the label ────────────────────────────────────────────────────────

def test_display_is_short_name_dash_company_name(db, env):
    from app.core.company_resolution import company_display
    assert (company_display(env["eavpi"])
            == "EAVPI - East Asia Veterinary Products, Inc")


def test_display_does_not_repeat_a_name_identical_to_the_short_name(db, env):
    from app.core.company_resolution import company_display
    assert company_display(env["personal"]) == "PERSONAL"


def test_display_without_a_company_name_is_the_short_name(db, env):
    from app.core.company_resolution import company_display
    env["eavpi"].company_name = None
    assert company_display(env["eavpi"]) == "EAVPI"
    assert company_display(None) is None


def test_display_never_carries_region_or_area(db, env):
    from app.core.company_resolution import company_display
    label = company_display(env["exc_cav"])
    assert "CAVITE" not in label and "South Luzon" not in label


def test_row_label_with_region_and_area_is_unchanged(db, env):
    """company_label() still serves the Vehicle form's picker and the
    Vehicle Register report, both deliberately left as they are."""
    from app.core.company_resolution import company_label
    assert (company_label(env["exc_cav"])
            == "Excellence (South Luzon / CAVITE 2)")


# ── the dropdown ─────────────────────────────────────────────────────

def test_options_list_each_company_once(db, env):
    from app.core.company_resolution import company_options
    from app.modules.master_data.org.models import OrgCompany

    options = company_options(
        OrgCompany.query.filter_by(is_active=True).all())

    assert [o["label"] for o in options] == [
        "EAVPI - East Asia Veterinary Products, Inc",
        "Excellence - Excellence Poultry & Livestock Specialist Inc",
        "PERSONAL",
    ]


def test_option_id_is_the_companys_lowest_row_id(db, env):
    """Stable across requests, so a bookmarked or saved filter keeps
    pointing at the same option."""
    from app.core.company_resolution import company_options
    rows = [env["exc_nl2"], env["exc_sl3"], env["exc_cav"]]
    options = company_options(rows)
    assert len(options) == 1
    assert options[0]["id"] == env["exc_cav"].id
    assert options[0]["short_name"] == "Excellence"


def test_same_short_name_with_a_different_company_name_stays_separate(
        db, env):
    """Only rows that agree on BOTH names are the same company."""
    from app.core.company_resolution import company_options
    env["exc_nl2"].company_name = "Excellence North Trading Corp"
    options = company_options(
        [env["exc_cav"], env["exc_sl3"], env["exc_nl2"]])
    assert len(options) == 2


def test_names_differing_only_by_case_or_outer_spaces_are_one_company(
        db, env):
    from app.core.company_resolution import company_options
    env["exc_nl2"].short_name = " EXCELLENCE "
    options = company_options(
        [env["exc_cav"], env["exc_sl3"], env["exc_nl2"]])
    assert len(options) == 1


# ── what a filter means ──────────────────────────────────────────────

def _plates(predicate):
    return {v.plate_number for v in Vehicle.query.filter(predicate).all()}


@pytest.mark.parametrize("picked", ["exc_cav", "exc_sl3", "exc_nl2"])
def test_filter_covers_every_area_of_the_company(db, env, picked):
    """Whichever of the company's rows the id belongs to -- the
    dropdown's own, an older bookmark, an older mobile build."""
    from app.core.company_resolution import company_filter
    assert (_plates(company_filter(Vehicle, env[picked].id))
            == {"WF-CAV", "WF-NL2", "WF-SL3"})


def test_filter_does_not_reach_another_company(db, env):
    from app.core.company_resolution import company_filter
    assert _plates(company_filter(Vehicle, env["eavpi"].id)) == {"WF-EAV"}


def test_filter_with_no_company_is_always_true(db, env):
    from app.core.company_resolution import company_filter
    assert len(_plates(company_filter(Vehicle, None))) == 4


def test_filter_for_an_unknown_id_matches_nothing(db, env):
    from app.core.company_resolution import company_filter
    assert _plates(company_filter(Vehicle, 999999)) == set()


def test_group_ids_are_all_rows_of_the_company_active_or_not(db, env):
    """A deactivated area still holds that company's vehicles; hiding
    them would make the company total disagree with 'All companies'."""
    from app.core.company_resolution import company_group_ids
    OrgCompanyService().deactivate(env["exc_nl2"].id)
    assert company_group_ids(env["exc_cav"].id) == {
        env["exc_cav"].id, env["exc_sl3"].id, env["exc_nl2"].id}
    assert company_group_ids(env["eavpi"].id) == {env["eavpi"].id}
    assert company_group_ids(None) == set()
    assert company_group_ids(999999) == set()


def test_same_company_checks_one_record_against_a_filter(db, env):
    from app.core.company_resolution import company_group_ids
    ids = company_group_ids(env["exc_sl3"].id)
    assert env["v_cav"].company_id in ids
    assert env["v_eavpi"].company_id not in ids


# ── what must not change: access scopes stay one row ─────────────────

def test_exact_filter_matches_only_that_row(db, env):
    from app.core.company_resolution import exact_company_filter
    assert (_plates(exact_company_filter(Vehicle, env["exc_cav"].id))
            == {"WF-CAV"})
    assert (_plates(exact_company_filter(Vehicle, env["exc_sl3"].id))
            == {"WF-SL3"})


def test_org_company_scope_is_not_widened_to_the_whole_company(db, env):
    """A user granted Excellence / CAVITE 2 must still see only that
    area's vehicles in the Vehicle list."""
    from app.modules.user_management.models import User
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    user = User(username="wf_scope", email="wf_scope@x.com",
                password_hash="x")
    _db.session.add(user)
    _db.session.commit()
    UserOrgScopeService().assign(user.id, scope_type="ORG_COMPANY",
                                 company_id=env["exc_cav"].id)

    items, total = VehicleService().list_page(user=user, page=1,
                                              page_size=50)
    assert {v.plate_number for v in items} == {"WF-CAV"}


def test_vehicle_list_filter_by_company_covers_all_its_areas(db, env):
    items, total = VehicleService().list_page(
        company_id=env["exc_nl2"].id, page=1, page_size=50)
    assert ({v.plate_number for v in items}
            == {"WF-CAV", "WF-NL2", "WF-SL3"})
