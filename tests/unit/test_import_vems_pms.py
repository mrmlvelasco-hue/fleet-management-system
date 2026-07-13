import sys
sys.path.insert(0, "/home/claude/fms/scripts")

from import_vems_pms import import_pms, _parse_step, _split_scope_into_items


def test_parse_step_km():
    assert _parse_step("5KMS", {"KM": 1000, "KMS": 1000}) == 5000
    assert _parse_step("1KM", {"KM": 1000, "KMS": 1000}) == 1000
    assert _parse_step("0", {"KM": 1000, "KMS": 1000}) == 0
    assert _parse_step(None, {"KM": 1000, "KMS": 1000}) == 0


def test_parse_step_calendar():
    assert _parse_step("3MTH", {"MTH": 30, "YRS": 365}) == 90
    assert _parse_step("2YRS", {"MTH": 30, "YRS": 365}) == 730


def test_split_scope_into_items():
    text = "1. Replace oil.  2. Check brakes.  3. Inspect tires."
    items = _split_scope_into_items(text)
    assert items == ["Replace oil.", "Check brakes.", "Inspect tires."]


def test_split_scope_handles_two_digit_numbers():
    text = "01. First step. 02. Second step. 23. Last step."
    items = _split_scope_into_items(text)
    assert len(items) == 3
    assert items[-1] == "Last step."


def test_dry_run_reports_stats_without_writing(db):
    stats = import_pms(
        "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx",
        dry_run=True, limit_groups=20)
    assert stats["groups_processed"] > 0
    assert stats["packages_created"] > 0

    from app.modules.maintenance_config.models import PMSchedule
    assert PMSchedule.query.count() == 0


def test_vehicle_registration_category_excluded(db):
    stats = import_pms(
        "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx",
        dry_run=True, limit_groups=None)
    assert stats["groups_skipped_excluded"] > 0


def test_real_import_limited_creates_schedules_and_scope_items(db):
    # First import the brand/model master data this depends on.
    sys.path.insert(0, "/home/claude/fms/scripts")
    from import_vems_makemodel import import_make_model
    import_make_model("/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx",
                      dry_run=False)

    stats = import_pms(
        "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx",
        dry_run=False, limit_groups=10)
    assert stats["packages_created"] > 0

    from app.modules.maintenance_config.models import PMSchedule, PMScopeTemplate
    assert PMSchedule.query.count() == stats["packages_created"]
    assert PMScopeTemplate.query.count() > 0

    # Confirm at least one schedule resolved via real FK brand/model match
    fk_matched = PMSchedule.query.filter(
        PMSchedule.vehicle_brand_id.isnot(None)).count()
    assert fk_matched > 0
