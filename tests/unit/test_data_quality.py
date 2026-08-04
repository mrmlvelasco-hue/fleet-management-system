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
    from app.modules.master_data.vehicle.models import Vehicle
    registered = {f.field_name for f in DataQualityField.query.all()}
    expected = {c.name for c in Vehicle.__table__.columns} - EXCLUDED_FIELDS
    assert registered == expected


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
