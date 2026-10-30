"""Fleet Data Quality Scorecard.

Configurable by design: which fields count, whether each is mandatory,
and what each is worth are database rows an administrator maintains, not
constants in code.
"""
import pytest

from app.core.data_quality_service import (
    DataQualityField, DataQualityService, EXCLUDED_FIELDS)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def graded(db):
    vt = VehicleTypeService().create(code="LV-DQ", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-DQ", name="Branch DQ")
    full = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="DQ-1",
        plate_number="DQA-111", chassis_number="CH1", engine_number="EN1")
    sparse = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Ranger", year=2021,
        branch_id=branch.id, conduction_number="DQ-2")
    db.session.commit()
    DataQualityService().sync_fields()
    db.session.commit()
    return full, sparse


def _only(db, *names, weights=None):
    """Restrict scoring to a named set, so a test asserts on fields it
    controls rather than on all 65."""
    for field in DataQualityField.query.all():
        field.include_in_score = field.field_name in names
        if weights and field.field_name in weights:
            field.weight = weights[field.field_name]
    db.session.commit()


def test_sync_registers_every_business_field(db, graded):
    """Every Vehicle column outside EXCLUDED_FIELDS, exactly -- plus, since
    ffa3b31 ("data quality can score vehicle attachments by document
    type"), virtual attachment:<CODE> fields: a catch-all, the built-in
    document types, and any document type added in Lookup Maintenance.
    Those are not columns, so they are checked separately."""
    from app.modules.master_data.vehicle.models import Vehicle
    from app.core.data_quality_service import (
        ANY_ATTACHMENT, ATTACHMENT_PREFIX, DEFAULT_ATTACHMENT_TYPES)
    registered = {f.field_name for f in DataQualityField.query.all()}
    columns = {n for n in registered if not n.startswith(ATTACHMENT_PREFIX)}
    attachments = registered - columns
    expected = {c.name for c in Vehicle.__table__.columns} - EXCLUDED_FIELDS
    assert columns == expected
    assert ANY_ATTACHMENT in attachments
    assert {ATTACHMENT_PREFIX + code for code, _ in DEFAULT_ATTACHMENT_TYPES} \
        <= attachments


def test_plumbing_columns_are_never_scoreable(db, graded):
    """id/created_at and friends are always populated; including them
    would only inflate every score."""
    registered = {f.field_name for f in DataQualityField.query.all()}
    assert not (registered & EXCLUDED_FIELDS)


def test_sync_never_overwrites_an_administrator_choice(db, graded):
    field = DataQualityField.query.filter_by(field_name="color").first()
    field.include_in_score = False
    field.is_required = True
    field.weight = 42
    db.session.commit()

    DataQualityService().sync_fields()
    db.session.commit()
    db.session.expire_all()

    field = DataQualityField.query.filter_by(field_name="color").first()
    assert field.include_in_score is False
    assert field.is_required is True
    assert field.weight == 42


def test_score_reflects_which_fields_are_filled(db, graded):
    full, sparse = graded
    _only(db, "chassis_number", "engine_number")
    svc = DataQualityService()
    assert svc.score_vehicle(full)["score"] == 100.0
    assert svc.score_vehicle(sparse)["score"] == 0.0


def test_weights_are_relative_not_percentages(db, graded):
    """A field with weight 10 counts twice one with weight 5."""
    full, sparse = graded
    _only(db, "chassis_number", "color",
          weights={"chassis_number": 10, "color": 5})
    # chassis filled, color empty -> 10 of 15
    assert DataQualityService().score_vehicle(full)["score"] == 66.7


def test_excluding_a_field_removes_it_from_the_score(db, graded):
    full, sparse = graded
    _only(db, "chassis_number")
    assert DataQualityService().score_vehicle(sparse)["score"] == 0.0
    _only(db, "brand")           # sparse HAS a brand
    assert DataQualityService().score_vehicle(sparse)["score"] == 100.0


def test_blank_and_whitespace_count_as_missing(db, graded):
    """A space typed into a text box is not data -- counting it as
    filled is how a completeness score becomes flattering and useless."""
    full, sparse = graded
    full.color = "   "
    db.session.commit()
    _only(db, "color")
    assert DataQualityService().score_vehicle(full)["score"] == 0.0


def test_false_boolean_counts_as_answered(db, graded):
    """False is a real answer, not an absence."""
    full, sparse = graded
    full.has_ctpl = False
    db.session.commit()
    _only(db, "has_ctpl")
    assert DataQualityService().score_vehicle(full)["score"] == 100.0


def test_required_fields_are_reported_separately(db, graded):
    full, sparse = graded
    _only(db, "chassis_number", "color")
    for name in ("chassis_number", "color"):
        DataQualityField.query.filter_by(field_name=name).first().is_required = True
    db.session.commit()

    result = DataQualityService().score_vehicle(sparse)
    assert {f.field_name for f in result["missing_required"]} == {
        "chassis_number", "color"}


def test_branch_scorecard_ranks_worst_first(db, graded):
    """The screen exists to show where attention is needed."""
    _only(db, "chassis_number")
    rows = DataQualityService().branch_scorecard()
    assert rows
    assert rows == sorted(rows, key=lambda r: r["score"])
    assert rows[0]["rank"] == 1


def test_rating_bands_match_the_specification(db, graded):
    svc = DataQualityService()
    assert svc.rating_of(100) == "EXCELLENT"
    assert svc.rating_of(90) == "EXCELLENT"
    assert svc.rating_of(89.9) == "WARNING"
    assert svc.rating_of(70) == "WARNING"
    assert svc.rating_of(69.9) == "CRITICAL"


def test_field_completion_is_ordered_worst_first(db, graded):
    _only(db, "chassis_number", "brand")
    rows = DataQualityService().field_completion()
    assert rows == sorted(rows, key=lambda r: r["rate"])


def test_drilldown_lists_only_vehicles_with_gaps(db, graded):
    full, sparse = graded
    _only(db, "chassis_number")
    rows = DataQualityService().vehicles_with_gaps()
    ids = {r["vehicle"].id for r in rows}
    assert sparse.id in ids
    assert full.id not in ids      # complete on the scored field


def test_no_scored_fields_does_not_divide_by_zero(db, graded):
    full, sparse = graded
    for field in DataQualityField.query.all():
        field.include_in_score = False
    db.session.commit()
    result = DataQualityService().score_vehicle(full)
    assert result["score"] == 0
    assert DataQualityService().branch_scorecard() == []


# ---------------------------------- the branch filter (2026-10-26)

@pytest.fixture()
def two_branches(db):
    """A big, sparse branch and a small, well-filled one -- so a filtered
    figure cannot coincidentally equal the fleet-wide one."""
    vt = VehicleTypeService().create(code="LV-BR", name="Light",
                                     category="LIGHT")
    big = BranchService().create(code="BIG", name="Manila Hub")
    small = BranchService().create(code="SML", name="East Asia Labs")
    for i in range(6):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
            branch_id=big.id, conduction_number=f"BR-BIG-{i}")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Camry", year=2024,
        branch_id=small.id, conduction_number="BR-SML-0",
        plate_number="SML-000", chassis_number="CH-S", engine_number="EN-S")
    db.session.commit()
    DataQualityService().sync_fields()
    db.session.commit()
    _only(db, "plate_number", "chassis_number", "engine_number")
    return big, small


def test_summary_counts_only_the_branch_asked_for(db, two_branches):
    """Reported at kickoff: picking a branch changed nothing above the
    drill-down list. summary() took no branch_id at all, so the tiles
    were always fleet-wide while the control implied otherwise."""
    big, small = two_branches
    svc = DataQualityService()

    everywhere = svc.summary()
    assert everywhere["vehicles"] == 7

    just_small = svc.summary(branch_id=small.id)
    assert just_small["vehicles"] == 1
    # That one vehicle has all three scored fields filled.
    assert just_small["score"] == 100
    assert just_small["incomplete"] == 0
    assert just_small["missing_points"] == 0

    just_big = svc.summary(branch_id=big.id)
    assert just_big["vehicles"] == 6
    assert just_big["incomplete"] == 6
    assert just_big["score"] < everywhere["score"] < just_small["score"]


def test_field_completion_counts_only_the_branch_asked_for(db, two_branches):
    big, small = two_branches
    svc = DataQualityService()

    small_rows = {f["label"]: f for f in svc.field_completion(branch_id=small.id)}
    plate = small_rows["Plate Number"]
    assert plate["filled"] == 1 and plate["missing"] == 0
    assert plate["rate"] == 100

    big_rows = {f["label"]: f for f in svc.field_completion(branch_id=big.id)}
    assert big_rows["Plate Number"]["missing"] == 6
    assert big_rows["Plate Number"]["rate"] == 0


def test_the_fleet_wide_figures_are_unchanged_when_no_branch_is_asked_for(
        db, two_branches):
    """The default must stay exactly what it was."""
    svc = DataQualityService()
    assert svc.summary()["vehicles"] == 7
    rows = {f["label"]: f for f in svc.field_completion()}
    assert rows["Plate Number"]["filled"] == 1
    assert rows["Plate Number"]["missing"] == 6


def test_the_endpoint_scopes_its_figures_to_the_selected_branch(
        db, client, two_branches):
    """End to end, through the route the screen actually calls."""
    import json

    from app.core.security.password import hash_password
    from app.core.security.registry import sync_permissions
    from app.extensions import db as _db
    from app.modules.user_management.models import Permission, Role, User

    sync_permissions()
    _db.session.commit()
    role = Role(name="DQ Reader")
    role.permissions = Permission.query.filter(
        Permission.code == "dataquality.view").all()
    user = User(username="dqr", email="dqr@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()

    big, small = two_branches
    r = client.post("/api/v1/auth/token",
                    json={"username": "dqr", "password": "secret123"})
    token = json.loads(r.get_data(as_text=True))["access_token"]
    head = {"Authorization": f"Bearer {token}"}

    everywhere = json.loads(client.get(
        "/api/v1/admin/data-quality", headers=head).get_data(as_text=True))
    scoped = json.loads(client.get(
        f"/api/v1/admin/data-quality?branch_id={small.id}",
        headers=head).get_data(as_text=True))

    assert everywhere["summary"]["vehicles"] == 7
    assert scoped["summary"]["vehicles"] == 1
    assert scoped["summary"]["score"] != everywhere["summary"]["score"]

    scoped_plate = next(f for f in scoped["fields"]
                        if f["label"] == "Plate Number")
    assert scoped_plate["missing"] == 0

    # The branch scorecard still ranks EVERY branch: it is the
    # comparison, and filtering it to one row would leave nothing to
    # compare against. The drill-down keeps following the selection.
    assert len(scoped["branches"]) == 2
    assert scoped["selected_branch_id"] == small.id
    assert len(scoped["gaps"]) == 0  # the one small-branch vehicle is complete


def test_a_scoped_call_is_not_served_the_fleet_wide_cached_result(
        db, app, two_branches):
    """summary() and field_completion() are @request_cached. If the
    cache keyed on the method alone, the first call in a request would
    answer every later one -- so a branch filter applied after a
    fleet-wide read would silently show the whole fleet's numbers under
    one branch's name. The decorator folds arguments into its key; this
    pins that, because the bug it prevents is invisible by inspection.
    """
    _big, small = two_branches
    with app.test_request_context("/"):
        svc = DataQualityService()
        everywhere = svc.summary()
        scoped = svc.summary(branch_id=small.id)
        assert everywhere["vehicles"] == 7
        assert scoped["vehicles"] == 1, (
            "a branch-scoped summary was served the fleet-wide result")

        all_fields = {f["label"]: f for f in svc.field_completion()}
        scoped_fields = {f["label"]: f
                         for f in svc.field_completion(branch_id=small.id)}
        assert all_fields["Plate Number"]["missing"] == 6
        assert scoped_fields["Plate Number"]["missing"] == 0


@pytest.fixture()
def company_graded(db):
    """A Company-only vehicle (branch_id intentionally NULL since Batch
    1) alongside a normal branch-anchored one, so the scorecard's
    'Branch' field can be checked against the real org-anchor rule
    rather than the raw column (2026-10-31, Batch 6)."""
    from app.modules.master_data.org.service import (
        RegionService, AreaDistrictService, OrgCompanyService)

    vt = VehicleTypeService().create(code="LV-DQ2", name="Light DQ2",
                                     category="LIGHT")
    region = RegionService().create(code="NL-DQ2", name="North Luzon DQ2")
    area = AreaDistrictService().create(code="NL2-DQ2", name="North Luzon 2 DQ2",
                                        region_id=region.id)
    co = OrgCompanyService().create(short_name="CoDQ2", region_id=region.id,
                                    area_district_id=area.id)
    branch = BranchService().create(code="BR-DQ2", name="Branch DQ2",
                                    company_id=co.id)

    branch_anchored = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="DQ2-BR",
        plate_number="DQ2A-111", chassis_number="CH1", engine_number="EN1")
    company_only = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Ranger", year=2021,
        company_id=co.id, conduction_number="DQ2-CO",
        plate_number="DQ2A-222", chassis_number="CH2", engine_number="EN2")
    db.session.commit()
    DataQualityService().sync_fields()
    db.session.commit()
    return co, branch, branch_anchored, company_only


def test_a_company_only_vehicle_is_not_flagged_as_missing_branch(
        db, company_graded):
    """The whole point of the fix: a vehicle anchored via Company alone
    must not be penalised for having no Branch -- it IS organisationally
    anchored, just not through Branch."""
    co, branch, branch_anchored, company_only = company_graded
    svc = DataQualityService()
    result = svc.score_vehicle(company_only)
    missing_names = {f.field_name for f in result["missing"]}
    assert "branch_id" not in missing_names


def test_a_branch_anchored_vehicle_still_counts_branch_as_filled(
        db, company_graded):
    co, branch, branch_anchored, company_only = company_graded
    svc = DataQualityService()
    result = svc.score_vehicle(branch_anchored)
    missing_names = {f.field_name for f in result["missing"]}
    assert "branch_id" not in missing_names


def test_an_unanchored_vehicle_still_counts_branch_as_missing(db, company_graded):
    """Make sure the fix doesn't just always say 'filled' -- a vehicle
    with NEITHER Branch nor Company must still be flagged."""
    co, branch, branch_anchored, company_only = company_graded
    vt = VehicleTypeService().create(code="LV-DQ2-UNANCHORED",
                                     name="Light DQ2 Unanchored",
                                     category="LIGHT")
    unanchored = VehicleService().create(
        vehicle_type_id=vt.id, brand="Mazda", model="BT-50", year=2020,
        conduction_number="DQ2-UN")
    svc = DataQualityService()
    result = svc.score_vehicle(unanchored)
    missing_names = {f.field_name for f in result["missing"]}
    assert "branch_id" in missing_names


def test_summary_can_be_scoped_to_a_company(db, company_graded):
    co, branch, branch_anchored, company_only = company_graded
    svc = DataQualityService()
    scoped = svc.summary(company_id=co.id)
    # Both vehicles resolve to this Company: one directly, one through
    # its mapped Branch.
    assert scoped["vehicles"] == 2


def test_field_completion_can_be_scoped_to_a_company(db, company_graded):
    co, branch, branch_anchored, company_only = company_graded
    svc = DataQualityService()
    rows = {f["label"]: f for f in svc.field_completion(company_id=co.id)}
    assert rows["Plate Number"]["filled"] == 2


def test_the_endpoint_scopes_its_figures_to_the_selected_company(
        db, client, company_graded):
    """End to end, through the route the screen actually calls -- the
    Company counterpart of test_the_endpoint_scopes_its_figures_to_the_
    selected_branch above."""
    import json

    from app.core.security.password import hash_password
    from app.core.security.registry import sync_permissions
    from app.extensions import db as _db
    from app.modules.user_management.models import Permission, Role, User

    co, branch, branch_anchored, company_only = company_graded
    sync_permissions()
    _db.session.commit()
    role = Role(name="DQ Reader Co")
    role.permissions = Permission.query.filter(
        Permission.code == "dataquality.view").all()
    user = User(username="dqrco", email="dqrco@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()

    r = client.post("/api/v1/auth/token",
                    json={"username": "dqrco", "password": "secret123"})
    token = json.loads(r.get_data(as_text=True))["access_token"]
    head = {"Authorization": f"Bearer {token}"}

    scoped = json.loads(client.get(
        f"/api/v1/admin/data-quality?company_id={co.id}",
        headers=head).get_data(as_text=True))

    assert scoped["summary"]["vehicles"] == 2
    assert scoped["selected_company_id"] == co.id
