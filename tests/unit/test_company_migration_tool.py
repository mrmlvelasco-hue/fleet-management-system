"""Stage 3B (D6): the Branch -> Company migration tool (dry-run review
report -> apply), backfilling vehicles.company_id from branch_id against
a Branch/Company mapping sheet.

Built and tested now against SYNTHETIC data per Michael's own request
(2026-10-29: "build for your testing 16 company but may duplicate
because of different area") while the real company list + Branch->
Company mapping sheet is still pending -- so this fixture deliberately
mirrors his real company list's own shape: 16 companies, with at least
one short_name repeated under a DIFFERENT Region/Area-District (D2),
exactly the case the per-(short_name, region, area) uniqueness exists
to handle.
"""
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.extensions import db as _db
from app.modules.master_data.org.company_migration import (
    import_company_mapping)
from app.modules.master_data.org.models import OrgCompany
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService


def _xlsx(rows, header=("branch_code", "company_short_name", "company_name",
                        "region_code", "area_district_code")):
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@pytest.fixture()
def vt(db):
    return VehicleTypeService().create(code="LV-MIG", name="Light",
                                       category="LIGHT")


def _branch(code):
    return BranchService().create(code=code, name=f"Branch {code}")


def _vehicle(vt, branch, cond, company_id=None):
    kwargs = {}
    if company_id is not None:
        kwargs["company_id"] = company_id
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number=cond, **kwargs)


# ── Core happy path ──────────────────────────────────────────────────

def test_dry_run_previews_without_writing_anything(db, vt):
    branch = _branch("MIG-001")
    _vehicle(vt, branch, "MIG-V1")
    region = RegionService().create(code="RG-MIG1", name="Luzon Mig")
    AreaDistrictService().create(code="AD-MIG1", name="San Mateo Mig",
                                 region_id=region.id)

    file_stream = _xlsx([("MIG-001", "Excellence Mig", "Excellence Mig Corp",
                          "RG-MIG1", "AD-MIG1")])
    result = import_company_mapping(file_stream, dry_run=True)

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert len(result["companies_created"]) == 1
    assert result["vehicles_updated"] == 1
    assert result["errors"] == []
    # Nothing actually written.
    assert OrgCompany.query.count() == 0


def test_apply_creates_the_company_and_backfills_the_vehicle(db, vt):
    branch = _branch("MIG-002")
    vehicle = _vehicle(vt, branch, "MIG-V2")
    region = RegionService().create(code="RG-MIG2", name="Visayas Mig")
    area = AreaDistrictService().create(code="AD-MIG2", name="Bacolod Mig",
                                        region_id=region.id)

    file_stream = _xlsx([("MIG-002", "Excellence Mig2", None,
                          "RG-MIG2", "AD-MIG2")])
    result = import_company_mapping(file_stream, dry_run=False)

    assert result["applied"] is True
    assert result["vehicles_updated"] == 1
    company = OrgCompany.query.filter_by(short_name="Excellence Mig2").first()
    assert company is not None
    assert company.region_id == region.id
    assert company.area_district_id == area.id
    # legacy_branch_id records which Branch this new company came from.
    assert company.legacy_branch_id == branch.id
    _db.session.refresh(vehicle)
    assert vehicle.company_id == company.id


def test_apply_never_overwrites_a_manually_assigned_company(db, vt):
    """Stage 3A already lets an admin set Vehicle.company_id manually.
    The migration must never clobber that, even when its own branch
    mapping would point somewhere else."""
    branch = _branch("MIG-003")
    region = RegionService().create(code="RG-MIG3", name="Luzon Mig3")
    area = AreaDistrictService().create(code="AD-MIG3", name="Area Mig3",
                                        region_id=region.id)
    manual_company = OrgCompanyService().create(
        short_name="Manually Set Co", region_id=region.id,
        area_district_id=area.id)
    vehicle = _vehicle(vt, branch, "MIG-V3", company_id=manual_company.id)

    file_stream = _xlsx([("MIG-003", "Some Other Co", None,
                          "RG-MIG3", "AD-MIG3")])
    result = import_company_mapping(file_stream, dry_run=False)

    assert result["vehicles_already_assigned_skipped"] == 1
    assert result["vehicles_updated"] == 0
    _db.session.refresh(vehicle)
    assert vehicle.company_id == manual_company.id


# ── D2: same short_name, different Region/Area-District ────────────

def test_duplicate_short_name_resolves_to_two_distinct_companies(db, vt):
    """Michael's own example (D2): the same short_name/company_name is
    allowed to repeat under a DIFFERENT Region + Area/District -- two
    distinct, valid company records, each with its own branch/vehicles
    correctly routed to the right one."""
    branch_luzon = _branch("MIG-DUP-L")
    branch_visayas = _branch("MIG-DUP-V")
    v_luzon = _vehicle(vt, branch_luzon, "MIG-DUP-VL")
    v_visayas = _vehicle(vt, branch_visayas, "MIG-DUP-VV")

    region_l = RegionService().create(code="RG-DUP-L", name="Luzon Dup")
    area_l = AreaDistrictService().create(code="AD-DUP-L", name="San Mateo Dup",
                                          region_id=region_l.id)
    region_v = RegionService().create(code="RG-DUP-V", name="Visayas Dup")
    area_v = AreaDistrictService().create(code="AD-DUP-V", name="Bacolod Dup",
                                          region_id=region_v.id)

    file_stream = _xlsx([
        ("MIG-DUP-L", "Excellence", None, "RG-DUP-L", "AD-DUP-L"),
        ("MIG-DUP-V", "Excellence", None, "RG-DUP-V", "AD-DUP-V"),
    ])
    result = import_company_mapping(file_stream, dry_run=False)

    assert result["vehicles_updated"] == 2
    companies = OrgCompany.query.filter_by(short_name="Excellence").all()
    assert len(companies) == 2
    luzon_company = next(c for c in companies if c.region_id == region_l.id)
    visayas_company = next(c for c in companies if c.region_id == region_v.id)
    assert luzon_company.id != visayas_company.id
    _db.session.refresh(v_luzon)
    _db.session.refresh(v_visayas)
    assert v_luzon.company_id == luzon_company.id
    assert v_visayas.company_id == visayas_company.id


def test_two_branches_mapped_to_the_same_company_share_one_record(db, vt):
    """The inverse case: several branches consolidating under ONE
    company -- must not create a duplicate company row for the second
    branch that names the identical (short_name, region, area) triple."""
    branch_1 = _branch("MIG-SAME-1")
    branch_2 = _branch("MIG-SAME-2")
    v1 = _vehicle(vt, branch_1, "MIG-SAME-V1")
    v2 = _vehicle(vt, branch_2, "MIG-SAME-V2")
    region = RegionService().create(code="RG-SAME", name="Region Same")
    AreaDistrictService().create(code="AD-SAME", name="Area Same",
                                 region_id=region.id)

    file_stream = _xlsx([
        ("MIG-SAME-1", "Shared Co", None, "RG-SAME", "AD-SAME"),
        ("MIG-SAME-2", "Shared Co", None, "RG-SAME", "AD-SAME"),
    ])
    result = import_company_mapping(file_stream, dry_run=False)

    assert OrgCompany.query.filter_by(short_name="Shared Co").count() == 1
    company = OrgCompany.query.filter_by(short_name="Shared Co").first()
    _db.session.refresh(v1)
    _db.session.refresh(v2)
    assert v1.company_id == company.id
    assert v2.company_id == company.id
    # legacy_branch_id records only the FIRST branch that created it.
    assert company.legacy_branch_id == branch_1.id


# ── Error handling: never guess ─────────────────────────────────────

def test_unresolved_branch_is_a_clear_error_and_nothing_else_breaks(db, vt):
    region = RegionService().create(code="RG-ERR1", name="Region Err1")
    AreaDistrictService().create(code="AD-ERR1", name="Area Err1",
                                 region_id=region.id)
    file_stream = _xlsx([("NO-SUCH-BRANCH", "Err Co", None,
                          "RG-ERR1", "AD-ERR1")])
    result = import_company_mapping(file_stream, dry_run=True)

    assert result["errors"][0]["type"] == "unresolved_branch"
    assert result["companies_created"] == []


def test_unresolved_region_is_a_clear_error(db, vt):
    branch = _branch("MIG-ERR2")
    file_stream = _xlsx([("MIG-ERR2", "Err Co2", None,
                          "NO-SUCH-REGION", "AD-WHATEVER")])
    result = import_company_mapping(file_stream, dry_run=True)
    assert result["errors"][0]["type"] == "unresolved_region"


def test_unresolved_area_district_is_a_clear_error(db, vt):
    branch = _branch("MIG-ERR3")
    region = RegionService().create(code="RG-ERR3", name="Region Err3")
    file_stream = _xlsx([("MIG-ERR3", "Err Co3", None,
                          "RG-ERR3", "NO-SUCH-AREA")])
    result = import_company_mapping(file_stream, dry_run=True)
    assert result["errors"][0]["type"] == "unresolved_area_district"


def test_an_area_code_that_exists_but_under_a_different_region_is_unresolved(
        db, vt):
    """AreaDistrict codes are only unique WITHIN their Region -- an area
    code that is real, but under a different Region than the row
    names, must not silently cross-match."""
    branch = _branch("MIG-ERR4")
    region_a = RegionService().create(code="RG-ERR4A", name="Region Err4A")
    region_b = RegionService().create(code="RG-ERR4B", name="Region Err4B")
    AreaDistrictService().create(code="AD-ERR4", name="Area Err4",
                                 region_id=region_b.id)
    file_stream = _xlsx([("MIG-ERR4", "Err Co4", None,
                          "RG-ERR4A", "AD-ERR4")])
    result = import_company_mapping(file_stream, dry_run=True)
    assert result["errors"][0]["type"] == "unresolved_area_district"


def test_a_branch_mapped_to_two_different_companies_is_ambiguous_and_skipped(
        db, vt):
    branch = _branch("MIG-AMB")
    vehicle = _vehicle(vt, branch, "MIG-AMB-V")
    region = RegionService().create(code="RG-AMB", name="Region Amb")
    area_1 = AreaDistrictService().create(code="AD-AMB1", name="Area Amb1",
                                          region_id=region.id)
    area_2 = AreaDistrictService().create(code="AD-AMB2", name="Area Amb2",
                                          region_id=region.id)

    file_stream = _xlsx([
        ("MIG-AMB", "Amb Co A", None, "RG-AMB", "AD-AMB1"),
        ("MIG-AMB", "Amb Co B", None, "RG-AMB", "AD-AMB2"),
    ])
    result = import_company_mapping(file_stream, dry_run=False)

    assert all(e["type"] == "ambiguous_branch" for e in result["errors"])
    assert len(result["errors"]) == 2
    assert OrgCompany.query.count() == 0
    _db.session.refresh(vehicle)
    assert vehicle.company_id is None


# ── Idempotency ──────────────────────────────────────────────────────

def test_applying_the_same_file_twice_creates_no_duplicates_and_no_double_counts(
        db, vt):
    branch = _branch("MIG-IDEM")
    vehicle = _vehicle(vt, branch, "MIG-IDEM-V")
    region = RegionService().create(code="RG-IDEM", name="Region Idem")
    AreaDistrictService().create(code="AD-IDEM", name="Area Idem",
                                 region_id=region.id)
    file_stream_1 = _xlsx([("MIG-IDEM", "Idem Co", None,
                            "RG-IDEM", "AD-IDEM")])
    import_company_mapping(file_stream_1, dry_run=False)

    assert OrgCompany.query.filter_by(short_name="Idem Co").count() == 1

    file_stream_2 = _xlsx([("MIG-IDEM", "Idem Co", None,
                            "RG-IDEM", "AD-IDEM")])
    result = import_company_mapping(file_stream_2, dry_run=False)

    assert OrgCompany.query.filter_by(short_name="Idem Co").count() == 1
    assert len(result["companies_matched_existing"]) == 1
    assert result["vehicles_updated"] == 0
    assert result["vehicles_already_assigned_skipped"] == 1


# ── The actual 16-company synthetic dataset Michael asked for ──────

def test_sixteen_company_synthetic_dataset_with_duplicate_short_names(db, vt):
    """Builds and applies a 16-row synthetic mapping sheet shaped like
    Michael's real company list (Company/Region/Area-District), with
    two short_names deliberately repeated under a different Region +
    Area/District each -- exactly what he flagged ("may duplicate
    because of different area") -- while the real list is still
    pending. Every branch gets one vehicle; every vehicle must land on
    the correct, distinct company."""
    regions = {}
    areas = {}
    for code, name in [("RG1", "Luzon"), ("RG2", "Visayas"), ("RG3", "Mindanao")]:
        regions[code] = RegionService().create(code=f"RG16-{code}", name=name)
    for (rcode, acode, aname) in [
            ("RG1", "AD1", "San Mateo"), ("RG1", "AD2", "Quezon City"),
            ("RG2", "AD3", "Bacolod"), ("RG2", "AD4", "Iloilo"),
            ("RG3", "AD5", "Davao"), ("RG3", "AD6", "Cagayan de Oro")]:
        areas[acode] = AreaDistrictService().create(
            code=f"AD16-{acode}", name=aname, region_id=regions[rcode].id)

    # 16 rows: 14 unique short_names + "Excellence" repeated twice
    # (Luzon/San Mateo vs Visayas/Bacolod) + "Golden Grain" repeated
    # twice (Luzon/Quezon City vs Mindanao/Davao) = 16 branches total.
    rows_spec = [
        ("Excellence", "RG1", "AD1"), ("Excellence", "RG2", "AD3"),
        ("Golden Grain", "RG1", "AD2"), ("Golden Grain", "RG3", "AD5"),
        ("Sunrise Farms", "RG1", "AD1"), ("Blue Harvest", "RG1", "AD2"),
        ("Pacific Breeders", "RG2", "AD3"), ("Island Poultry", "RG2", "AD4"),
        ("Southern Livestock", "RG3", "AD5"), ("Highland Farms", "RG3", "AD6"),
        ("Coastal Feeds", "RG1", "AD1"), ("Valley Agri", "RG1", "AD2"),
        ("Mountain Fresh", "RG2", "AD3"), ("Lakeside Farms", "RG2", "AD4"),
        ("Tropical Grains", "RG3", "AD5"), ("Metro Feeds", "RG3", "AD6"),
    ]
    assert len(rows_spec) == 16

    branches = []
    vehicles = []
    rows = []
    for i, (short_name, rcode, acode) in enumerate(rows_spec, start=1):
        branch = _branch(f"MIG16-{i:02d}")
        branches.append(branch)
        vehicles.append(_vehicle(vt, branch, f"MIG16-V{i:02d}"))
        rows.append((branch.code, short_name, None,
                    f"RG16-{rcode}", f"AD16-{acode}"))

    file_stream = _xlsx(rows)
    result = import_company_mapping(file_stream, dry_run=False)

    assert result["errors"] == []
    assert result["vehicles_updated"] == 16
    assert OrgCompany.query.filter_by(short_name="Excellence").count() == 2
    assert OrgCompany.query.filter_by(short_name="Golden Grain").count() == 2
    assert OrgCompany.query.count() == 16  # 16 rows, no two share a triple

    for branch, vehicle, (short_name, rcode, acode) in zip(
            branches, vehicles, rows_spec):
        _db.session.refresh(vehicle)
        company = db.session.get(OrgCompany, vehicle.company_id)
        assert company is not None
        assert company.short_name == short_name
        assert company.region_id == regions[rcode].id
        assert company.area_district_id == areas[acode].id
