"""SQL-level pagination and visibility for Vendor.

Audit finding 2.1b (2026-11-08/09 performance audit): Vendor loads its
ENTIRE table into Python and paginates/filters there, same as
Tire/Battery/Driver before their 2026-11-09 fix -- but Vendor was
deliberately deferred from that fix because its visibility rule is a
many-to-many across FIVE axes (branches, business units, companies,
regions, area-districts: see app/modules/master_data/vendor/models.py),
not the single `branch_id`/`company_id` column shape
UserOrgScopeService.sql_visibility_clause() covers.

Every test here follows the same shape the Tire/Battery/Driver parity
tests use: the fast SQL path (list_page()) must return EXACTLY what the
slow, trusted Python path (list()/`_is_visible()`) returns. Given how
easy it is to miss one of VendorService._is_visible()'s and
UserOrgScopeService.covers_org()'s branches (an ORG_REGION scope also
covers a Company filed under it AND an Area-District filed under it
AND is covered BY an Area-District scope on an area within that
region -- three distinct reciprocal paths just for one axis), this
file deliberately exercises each axis and each cross-axis reciprocal
path individually rather than relying on one broad scenario.
"""
import pytest

from app.extensions import db
from app.modules.master_data.org.service import (
    AreaDistrictService, BranchService, BusinessUnitService,
    OrgCompanyService, RegionService)
from app.modules.master_data.vendor.service import VendorService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    north = RegionService().create(code="VND-N", name="North Region")
    south = RegionService().create(code="VND-S", name="South Region")

    north_a = AreaDistrictService().create(
        code="VND-NA", name="North Area A", region_id=north.id)
    north_b = AreaDistrictService().create(
        code="VND-NB", name="North Area B", region_id=north.id)
    south_a = AreaDistrictService().create(
        code="VND-SA", name="South Area A", region_id=south.id)

    co_na = OrgCompanyService().create(
        short_name="VND-CoNA", region_id=north.id, area_district_id=north_a.id)
    co_sa = OrgCompanyService().create(
        short_name="VND-CoSA", region_id=south.id, area_district_id=south_a.id)

    branch_na = BranchService().create(code="VND-BR-NA", name="Branch NA")
    branch_sa = BranchService().create(code="VND-BR-SA", name="Branch SA")

    bu1 = BusinessUnitService().create(code="VND-BU1", name="BU One")

    svc = VendorService()
    v_branch = svc.create(code="V-BRANCH", name="Branch Vendor")
    svc.assign_branches(v_branch.id, [branch_na.id])

    v_bu = svc.create(code="V-BU", name="BU Vendor")
    svc.assign_business_units(v_bu.id, [bu1.id])

    v_company = svc.create(code="V-COMPANY", name="Company Vendor")
    svc.assign_companies(v_company.id, [co_na.id])

    v_region = svc.create(code="V-REGION", name="Region Vendor")
    svc.assign_regions(v_region.id, [north.id])

    v_area = svc.create(code="V-AREA", name="Area Vendor")
    svc.assign_area_districts(v_area.id, [north_a.id])

    v_other_company = svc.create(code="V-OTHERCO", name="Other Company Vendor")
    svc.assign_companies(v_other_company.id, [co_sa.id])

    v_unassigned = svc.create(code="V-UNASSIGNED", name="Unassigned Vendor",
                              vendor_type="SERVICES")

    v_multi = svc.create(code="V-MULTI", name="Multi-axis Vendor")
    svc.assign_branches(v_multi.id, [branch_sa.id])
    svc.assign_companies(v_multi.id, [co_sa.id])

    users = {}
    for key, kwargs in {
        "branch": dict(scope_type="BRANCH", branch_id=branch_na.id),
        "bu": dict(scope_type="BUSINESS_UNIT", business_unit_id=bu1.id),
        "company": dict(scope_type="ORG_COMPANY", company_id=co_na.id),
        "region": dict(scope_type="ORG_REGION", region_id=north.id),
        "area": dict(scope_type="ORG_AREA_DISTRICT",
                     area_district_id=north_a.id),
        "other_area": dict(scope_type="ORG_AREA_DISTRICT",
                           area_district_id=south_a.id),
        "global": dict(scope_type="GLOBAL"),
    }.items():
        u = User(username=f"vndscope_{key}", email=f"vndscope_{key}@x.com",
                password_hash="x")
        db.session.add(u)
        db.session.commit()
        UserOrgScopeService().assign(u.id, **kwargs)
        users[key] = u

    unscoped = User(username="vndscope_unscoped",
                    email="vndscope_unscoped@x.com", password_hash="x")
    db.session.add(unscoped)
    db.session.commit()
    users["unscoped"] = unscoped

    creator = User(username="vndscope_creator",
                   email="vndscope_creator@x.com", password_hash="x")
    db.session.add(creator)
    db.session.commit()
    users["creator"] = creator
    # A vendor the creator user made themselves, restricted to a
    # Company they have no scope over -- created_by must still win.
    from flask import g
    v_own = svc.create(code="V-OWN", name="Own Vendor")
    v_own.created_by = creator.id
    svc.assign_companies(v_own.id, [co_sa.id])
    db.session.commit()

    return dict(
        north=north, south=south, north_a=north_a, north_b=north_b,
        south_a=south_a, co_na=co_na, co_sa=co_sa,
        branch_na=branch_na, branch_sa=branch_sa, bu1=bu1,
        v_branch=v_branch, v_bu=v_bu, v_company=v_company,
        v_region=v_region, v_area=v_area, v_other_company=v_other_company,
        v_unassigned=v_unassigned, v_multi=v_multi, v_own=v_own,
        **users,
    )


def _ids(rows):
    return {r.id for r in rows}


class TestVendorListPageParity:
    """Each test asserts list_page() returns EXACTLY what list() returns
    for the same user, not just that the expected vendor is present --
    a false positive from a too-loose SQL clause would still pass an
    'is in' assertion but fail this."""

    def _assert_parity(self, user, env):
        svc = VendorService()
        slow = _ids(svc.list(user=user))
        rows, total = svc.list_page(
            user=user, active=True, page=1, page_size=500)
        fast = _ids(rows)
        assert fast == slow, (
            f"list_page() returned {fast} but list() returned {slow} "
            f"for user={getattr(user, 'username', None)}")
        assert total == len(slow)
        return slow

    def test_unscoped_user_sees_everything(self, db, env):
        slow = self._assert_parity(env["unscoped"], env)
        assert env["v_branch"].id in slow
        assert env["v_other_company"].id in slow

    def test_global_scope_sees_everything(self, db, env):
        slow = self._assert_parity(env["global"], env)
        assert env["v_other_company"].id in slow

    def test_unassigned_vendor_always_visible(self, db, env):
        # Even a tightly-scoped user sees a vendor with no axis
        # assignments at all -- "no assignment = serves everyone".
        slow = self._assert_parity(env["branch"], env)
        assert env["v_unassigned"].id in slow

    def test_branch_scope(self, db, env):
        slow = self._assert_parity(env["branch"], env)
        assert slow == {env["v_branch"].id, env["v_unassigned"].id}

    def test_business_unit_scope(self, db, env):
        slow = self._assert_parity(env["bu"], env)
        assert slow == {env["v_bu"].id, env["v_unassigned"].id}

    def test_company_scope_matches_company_axis_directly(self, db, env):
        slow = self._assert_parity(env["company"], env)
        assert env["v_company"].id in slow
        assert env["v_other_company"].id not in slow

    def test_company_scope_also_covers_its_own_region_and_area_vendors(
            self, db, env):
        # co_na sits in north/north_a -- an ORG_COMPANY scope on co_na
        # must ALSO make v_region (assigned to `north`) and v_area
        # (assigned to `north_a`) visible, via covers_org()'s reciprocal
        # "a Company scope covers its own Region/Area" rule.
        slow = self._assert_parity(env["company"], env)
        assert env["v_region"].id in slow
        assert env["v_area"].id in slow

    def test_region_scope_matches_region_axis_directly(self, db, env):
        slow = self._assert_parity(env["region"], env)
        assert env["v_region"].id in slow

    def test_region_scope_covers_companies_and_areas_filed_under_it(
            self, db, env):
        # north contains co_na (company axis) and north_a (area axis) --
        # an ORG_REGION scope on `north` must cover vendors scoped to
        # either, via covers_org()'s "a Region scope covers Companies/
        # Areas filed under it" rule.
        slow = self._assert_parity(env["region"], env)
        assert env["v_company"].id in slow
        assert env["v_area"].id in slow
        assert env["v_other_company"].id not in slow

    def test_area_scope_matches_area_axis_directly(self, db, env):
        slow = self._assert_parity(env["area"], env)
        assert env["v_area"].id in slow

    def test_area_scope_covers_company_filed_under_it(self, db, env):
        # co_na's own area_district is north_a -- an ORG_AREA_DISTRICT
        # scope on north_a must cover v_company (assigned to co_na).
        slow = self._assert_parity(env["area"], env)
        assert env["v_company"].id in slow

    def test_area_scope_covers_its_own_parent_region(self, db, env):
        # north_a's own region is `north` -- an ORG_AREA_DISTRICT scope
        # on north_a must ALSO cover v_region (assigned to `north`),
        # via covers_org()'s "an Area scope covers its parent Region"
        # rule.
        slow = self._assert_parity(env["area"], env)
        assert env["v_region"].id in slow

    def test_area_scope_does_not_leak_into_a_different_areas_company(
            self, db, env):
        # south_a-scoped user must NOT see v_company (co_na, filed
        # under north/north_a) -- confirms the clause doesn't
        # over-match everything once any ORG_AREA_DISTRICT scope exists.
        slow = self._assert_parity(env["other_area"], env)
        assert env["v_company"].id not in slow
        assert env["v_other_company"].id in slow

    def test_multi_axis_vendor_matches_on_either_axis(self, db, env):
        # v_multi is scoped to BOTH branch_sa and co_sa -- a user
        # scoped only to co_sa's area (south_a) must still see it via
        # the company axis even though the branch axis doesn't match.
        south_user = env["other_area"]
        slow = self._assert_parity(south_user, env)
        assert env["v_multi"].id in slow

    def test_created_by_overrides_a_non_matching_scope(self, db, env):
        # v_own is scoped to co_sa, which `creator` has no org-scope
        # over at all -- but `creator` made it, so it must still be
        # visible to them specifically (not to other unscoped users --
        # that's covered by the "unscoped user sees everything"
        # scenario already being a separate, broader case).
        slow = self._assert_parity(env["creator"], env)
        assert env["v_own"].id in slow

    def test_pagination_is_applied_in_sql(self, db, env):
        svc = VendorService()
        rows, total = svc.list_page(page=1, page_size=1)
        assert len(rows) == 1
        assert total >= 8

    def test_search_matches_code_or_name(self, db, env):
        svc = VendorService()
        rows, total = svc.list_page(search="Region Vendor", page=1,
                                    page_size=500)
        assert _ids(rows) == {env["v_region"].id}

    def test_vendor_type_filter(self, db, env):
        svc = VendorService()
        rows, total = svc.list_page(vendor_type="SERVICES", page=1,
                                    page_size=500)
        assert _ids(rows) == {env["v_unassigned"].id}

    def test_active_filter(self, db, env):
        svc = VendorService()
        svc.deactivate(env["v_branch"].id)
        rows, total = svc.list_page(active=False, page=1, page_size=500)
        assert _ids(rows) == {env["v_branch"].id}
        rows2, total2 = svc.list_page(active=True, page=1, page_size=500)
        assert env["v_branch"].id not in _ids(rows2)


class TestVendorSummary:
    """summary() must count exactly the same set list_page() returns
    for the same filters/user -- the chips above the table and the
    table itself must never disagree."""

    def test_unfiltered_totals_match_unscoped_list(self, db, env):
        svc = VendorService()
        rows, total = svc.list_page(page=1, page_size=500)
        summary = svc.summary()
        assert summary["total"] == total == len(rows)
        assert summary["active"] == sum(1 for r in rows if r.is_active)
        assert summary["services"] == sum(
            1 for r in rows if r.vendor_type == "SERVICES")

    def test_scoped_summary_matches_scoped_list_page(self, db, env):
        svc = VendorService()
        rows, total = svc.list_page(
            user=env["region"], active=True, page=1, page_size=500)
        summary = svc.summary(user=env["region"], active=True)
        assert summary["total"] == total == len(rows)

    def test_vendor_type_filter_matches(self, db, env):
        svc = VendorService()
        rows, total = svc.list_page(
            vendor_type="SERVICES", page=1, page_size=500)
        summary = svc.summary(vendor_type="SERVICES")
        assert summary["total"] == total == len(rows) == 1

    def test_active_filter_matches(self, db, env):
        svc = VendorService()
        svc.deactivate(env["v_branch"].id)
        rows, total = svc.list_page(active=False, page=1, page_size=500)
        summary = svc.summary(active=False)
        assert summary["total"] == total == len(rows) == 1
