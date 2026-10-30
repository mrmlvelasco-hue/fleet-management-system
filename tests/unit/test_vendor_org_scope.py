"""Batch 7 (2026-10-31): Vendor scoping by Company, Region, and
Area/District, alongside the existing Branch/Business-Unit scoping
(tests/unit/test_vendor_multi_branch_scope.py).

Michael's decision: Vendor coverage must be configurable per Company,
Region, AND Area/District -- not only per Branch. The three new axes
are alternatives to Branch/BU, not a further narrowing: a vendor
matching ANY one assigned axis is visible (the same "any assignment is
enough" rule Branch/BU already follow), and a vendor with NOTHING
assigned on any of the five axes still serves everyone.
"""
import pytest

from app.modules.master_data.vendor.service import VendorService
from app.modules.master_data.org.service import (
    RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    region_a = RegionService().create(code="RG-VEN-A", name="Luzon Vendor")
    region_b = RegionService().create(code="RG-VEN-B", name="Visayas Vendor")
    area_a = AreaDistrictService().create(code="AD-VEN-A", name="San Mateo Vendor",
                                          region_id=region_a.id)
    area_b = AreaDistrictService().create(code="AD-VEN-B", name="Bacolod Vendor",
                                          region_id=region_b.id)
    company_a = OrgCompanyService().create(short_name="CoVenA", region_id=region_a.id,
                                           area_district_id=area_a.id)
    company_b = OrgCompanyService().create(short_name="CoVenB", region_id=region_b.id,
                                           area_district_id=area_b.id)

    company_scoped = VendorService().create(code="VEN-COMP", name="Company-scoped Shop")
    VendorService().assign_companies(company_scoped.id, [company_a.id])

    region_scoped = VendorService().create(code="VEN-REGION", name="All-of-Luzon Shop")
    VendorService().assign_regions(region_scoped.id, [region_a.id])

    area_scoped = VendorService().create(code="VEN-AREA", name="San Mateo Shop")
    VendorService().assign_area_districts(area_scoped.id, [area_a.id])

    nationwide = VendorService().create(code="VEN-NATIONWIDE-ORG", name="Nationwide Co")

    company_a_user = User(username="venorg_co_a", email="venorg_co_a@x.com",
                          password_hash="x")
    company_b_user = User(username="venorg_co_b", email="venorg_co_b@x.com",
                          password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([company_a_user, company_b_user])
    _db.session.commit()
    UserOrgScopeService().assign(company_a_user.id, scope_type="ORG_COMPANY",
                                 company_id=company_a.id)
    UserOrgScopeService().assign(company_b_user.id, scope_type="ORG_COMPANY",
                                 company_id=company_b.id)

    return (region_a, region_b, area_a, area_b, company_a, company_b,
           company_scoped, region_scoped, area_scoped, nationwide,
           company_a_user, company_b_user)


def test_assign_companies_to_vendor(db, env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    assert {c.id for c in company_scoped.companies} == {company_a.id}


def test_assign_regions_to_vendor(db, env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    assert {r.id for r in region_scoped.regions} == {region_a.id}


def test_assign_area_districts_to_vendor(db, env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    assert {a.id for a in area_scoped.area_districts} == {area_a.id}


def test_company_a_user_sees_the_company_scoped_vendor(db, env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    visible_ids = {v.id for v in VendorService().list(user=company_a_user)}
    assert company_scoped.id in visible_ids


def test_company_b_user_does_not_see_the_company_a_scoped_vendor(db, env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    visible_ids = {v.id for v in VendorService().list(user=company_b_user)}
    assert company_scoped.id not in visible_ids


def test_company_a_user_sees_the_region_scoped_vendor_covering_their_company(
        db, env):
    """Company A sits inside Region A -- a vendor assigned to the whole
    Region must be visible to a user scoped to one Company within it."""
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    visible_ids = {v.id for v in VendorService().list(user=company_a_user)}
    assert region_scoped.id in visible_ids


def test_company_b_user_does_not_see_the_region_a_scoped_vendor(db, env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    visible_ids = {v.id for v in VendorService().list(user=company_b_user)}
    assert region_scoped.id not in visible_ids


def test_company_a_user_sees_the_area_scoped_vendor_covering_their_company(
        db, env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    visible_ids = {v.id for v in VendorService().list(user=company_a_user)}
    assert area_scoped.id in visible_ids


def test_everyone_sees_the_nationwide_vendor(db, env):
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    assert nationwide.id in {v.id for v in VendorService().list(user=company_a_user)}
    assert nationwide.id in {v.id for v in VendorService().list(user=company_b_user)}


def test_a_vendor_with_only_a_company_assignment_is_hidden_from_an_unscoped_branch_user(
        db, env):
    """A BRANCH-scoped user whose scope names no Company/Region/Area
    at all must not see a Company-only-scoped vendor either -- Batch 5's
    lesson applies here too: an axis the user's scope says nothing about
    is not an automatic match."""
    (region_a, region_b, area_a, area_b, company_a, company_b,
     company_scoped, region_scoped, area_scoped, nationwide,
     company_a_user, company_b_user) = env
    from app.modules.master_data.org.service import BranchService
    branch = BranchService().create(code="BR-VENORG", name="Branch VenOrg")
    branch_user = User(username="venorg_branch", email="venorg_branch@x.com",
                       password_hash="x")
    from app.extensions import db as _db
    _db.session.add(branch_user)
    _db.session.commit()
    UserOrgScopeService().assign(branch_user.id, scope_type="BRANCH",
                                 branch_id=branch.id)
    visible_ids = {v.id for v in VendorService().list(user=branch_user)}
    assert company_scoped.id not in visible_ids
