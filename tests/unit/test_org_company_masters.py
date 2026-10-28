"""Stage 3A — Region / Area-District / Company master data.

Region and Area/District are configurable master tables (D3): Area
belongs to a Region, so a flat Lookup row cannot model it. Company
(D2) deliberately allows the SAME short_name/company_name to repeat
under a DIFFERENT Region + Area/District -- Michael's own example is
"Excellence" in Luzon/San Mateo and "Excellence" in Visayas/Bacolod,
two distinct records -- so uniqueness is on the (short_name, region,
area) business key, never on short_name alone.
"""
import pytest

from app.modules.master_data.org.service import (
    RegionService, AreaDistrictService, OrgCompanyService,
    DuplicateCodeError)


@pytest.fixture()
def env(db):
    luzon = RegionService().create(code="LUZ", name="Luzon")
    visayas = RegionService().create(code="VIS", name="Visayas")
    san_mateo = AreaDistrictService().create(
        code="SNM", name="San Mateo", region_id=luzon.id)
    bacolod = AreaDistrictService().create(
        code="BCD", name="Bacolod", region_id=visayas.id)
    return {"luzon": luzon, "visayas": visayas,
            "san_mateo": san_mateo, "bacolod": bacolod}


# ── Region ───────────────────────────────────────────────────────────────

def test_create_region(db, env):
    r = RegionService().create(code="MIN", name="Mindanao")
    assert r.id is not None
    assert r.is_active is True


def test_region_code_must_be_unique(db, env):
    with pytest.raises(DuplicateCodeError):
        RegionService().create(code="LUZ", name="Luzon Again")


def test_region_list_excludes_inactive_by_default(db, env):
    RegionService().deactivate(env["luzon"].id)
    codes = {r.code for r in RegionService().list()}
    assert "LUZ" not in codes
    assert "VIS" in codes


# ── Area / District ──────────────────────────────────────────────────────

def test_create_area_district_under_a_region(db, env):
    a = AreaDistrictService().create(
        code="QC", name="Quezon City", region_id=env["luzon"].id)
    assert a.region_id == env["luzon"].id


def test_area_district_code_unique_only_within_its_region(db, env):
    """The same code may exist in two different regions -- the
    uniqueness key is (code, region), not code alone."""
    a = AreaDistrictService().create(
        code="SNM", name="San Mateo (Visayas homonym)",
        region_id=env["visayas"].id)
    assert a.id is not None


def test_area_district_code_rejected_when_duplicated_in_same_region(db, env):
    with pytest.raises(DuplicateCodeError):
        AreaDistrictService().create(
            code="SNM", name="Dup", region_id=env["luzon"].id)


# ── Company ───────────────────────────────────────────────────────────────

def test_create_company(db, env):
    c = OrgCompanyService().create(
        short_name="Excellence", region_id=env["luzon"].id,
        area_district_id=env["san_mateo"].id,
        company_name="Excellence Poultry and Livestock Specialist Inc.")
    assert c.id is not None
    assert c.region_id == env["luzon"].id
    assert c.area_district_id == env["san_mateo"].id


def test_same_short_name_allowed_under_a_different_region_and_area(db, env):
    """Michael's own example: 'Excellence' in Luzon/San Mateo and
    'Excellence' in Visayas/Bacolod are two distinct records."""
    OrgCompanyService().create(
        short_name="Excellence", region_id=env["luzon"].id,
        area_district_id=env["san_mateo"].id)
    second = OrgCompanyService().create(
        short_name="Excellence", region_id=env["visayas"].id,
        area_district_id=env["bacolod"].id)
    assert second.id is not None


def test_same_short_name_rejected_under_the_identical_region_and_area(
        db, env):
    OrgCompanyService().create(
        short_name="Excellence", region_id=env["luzon"].id,
        area_district_id=env["san_mateo"].id)
    with pytest.raises(DuplicateCodeError):
        OrgCompanyService().create(
            short_name="Excellence", region_id=env["luzon"].id,
            area_district_id=env["san_mateo"].id)


def test_area_district_must_belong_to_the_given_region(db, env):
    """Bacolod belongs to Visayas -- pairing it with Luzon would create
    a company whose region and area silently disagree."""
    from app.modules.master_data.org.service import InvalidOrgDataError
    with pytest.raises(InvalidOrgDataError):
        OrgCompanyService().create(
            short_name="Mismatched", region_id=env["luzon"].id,
            area_district_id=env["bacolod"].id)


def test_company_requires_a_region_and_an_area_district(db, env):
    from app.modules.master_data.org.service import InvalidOrgDataError
    with pytest.raises(InvalidOrgDataError):
        OrgCompanyService().create(short_name="No Region", region_id=None,
                                   area_district_id=None)
