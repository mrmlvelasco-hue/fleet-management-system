"""Tests for vems_frequency_reference.py and import_pm_task_list.py --
locks in the specific data-quality corrections found in the source
frequency lookup table, and the interval-derivation model (each row's
own code is its real interval; NOT periodicity-inferred, since that
approach was tried first and found to be wrong for this file's actual
data -- see the module docstring in import_pm_task_list.py).
"""
import sys
sys.path.insert(0, "/home/claude/fms/scripts")

from vems_frequency_reference import (
    resolve_frequency, to_interval_km, to_interval_days, to_interval_hours)
from import_pm_task_list import import_pm_task_list, _split_scope_into_items


# ── Frequency reference corrections ─────────────────────────────────────────

def test_matches_the_spec_worked_example():
    # 4.5MTH -> 135 days is the exact example the migration spec used.
    assert to_interval_days("4.5MTH") == 135


def test_corrects_10kms_wrongly_coded_as_hours_in_the_source_table():
    assert to_interval_km("10KMS") == 10000
    assert to_interval_hours("10KMS") is None


def test_corrects_1km_wrongly_coded_as_hours_in_the_source_table():
    assert to_interval_km("1KM") == 1000


def test_corrects_20kms_which_had_both_wrong_value_and_wrong_unit():
    # Source table had Val=1, ValDesc=Y ("Every 20,000 Km" as 1 year!).
    assert to_interval_km("20KMS") == 20000
    assert to_interval_days("20KMS") is None


def test_corrects_35kms_wrongly_coded_as_hours():
    assert to_interval_km("35KMS") == 35000


def test_corrects_25kms_which_had_both_wrong_value_and_wrong_unit():
    # Source table had Val=15, ValDesc=M ("Every 25,000 km" as 15 months!).
    assert to_interval_km("25KMS") == 25000
    assert to_interval_days("25KMS") is None


def test_corrects_7_5kms_wrongly_coded_as_hours():
    assert to_interval_km("7.5KMS") == 7500


def test_correctly_unaffected_codes_still_resolve_correctly():
    """Sanity check that fixing the broken rows didn't disturb any of
    the rows that were already correct."""
    assert to_interval_km("5KMS") == 5000
    assert to_interval_km("2KMS") == 2000
    assert to_interval_km("45KMS") == 45000
    assert to_interval_km("8KMS") == 8000
    assert to_interval_km("3KMS") == 3000
    assert to_interval_km("1.5KM") == 1500
    assert to_interval_days("1MTH") == 30
    assert to_interval_days("3MTH") == 90
    assert to_interval_days("1YR") == 365
    assert to_interval_days("2YRS") == 730
    assert to_interval_days("1WK") == 7
    assert to_interval_days("1QTR") == 90
    assert to_interval_hours("500HRS") == 500


def test_unrecognized_code_returns_none_not_zero():
    """None (not 0) is the correct 'not applicable' signal -- a caller
    must be able to tell 'unrecognized' apart from 'a real zero
    interval', which would mean something entirely different."""
    value, unit = resolve_frequency("NOTAREALCODE")
    assert value is None and unit is None
    assert to_interval_km("NOTAREALCODE") is None
    assert to_interval_days("NOTAREALCODE") is None


def test_zero_and_blank_codes_resolve_to_none():
    for code in (None, "0", 0, "", "NaN"):
        assert to_interval_km(code) is None
        assert to_interval_days(code) is None


# ── Importer: correct per-row interval derivation ───────────────────────────

def test_scope_split_handles_numbered_checklist():
    text = "1. Replace oil.  2. Check brakes.  3. Inspect tires."
    assert _split_scope_into_items(text) == [
        "Replace oil.", "Check brakes.", "Inspect tires."]


def test_dry_run_ford_escape_intervals_match_their_own_work_description(db):
    """Regression test for the actual bug caught while building this:
    the first attempt inferred each package's interval by multiplying a
    group-wide 'base step' by a periodicity derived from repeated Scope
    text -- which produced e.g. 10,000km for a row whose own
    WorkDescription literally says '5,000 km servicing'. Every package's
    interval_km must equal what ITS OWN row's KM Reading code decodes
    to, independent of any other row in the same Task_CD group."""
    stats = import_pm_task_list(
        "/mnt/user-data/uploads/PM_Task_List.xlsx",
        dry_run=True, limit_groups=1)
    assert stats["groups_processed"] == 1

    ford_escape_samples = [s for s in stats["samples"] if s["make"] == "Ford"]
    assert ford_escape_samples, "expected the first group to be Ford Escape"

    for s in ford_escape_samples:
        wd = s["work_description"]
        if s["interval_km"] == 1000:
            assert "First 1,000" in wd or "First 1000" in wd
        elif "5,000 km" in wd:
            assert s["interval_km"] == 5000
        elif "10,000 km" in wd:
            assert s["interval_km"] == 5000  # its OWN code is 5KMS, not 10,000


def test_dry_run_reports_no_unrecognized_frequency_codes(db):
    """Every code actually present in the real file must be covered by
    the corrected reference table -- this is the whole file, not a
    sample, so it's the real completeness check."""
    stats = import_pm_task_list(
        "/mnt/user-data/uploads/PM_Task_List.xlsx", dry_run=True)
    assert stats["unrecognized_frequency_codes"] == []


def test_vehicle_registration_category_excluded(db):
    stats = import_pm_task_list(
        "/mnt/user-data/uploads/PM_Task_List.xlsx", dry_run=True)
    assert stats["groups_skipped_excluded"] > 0


def test_real_import_captures_work_description_template(db):
    stats = import_pm_task_list(
        "/mnt/user-data/uploads/PM_Task_List.xlsx",
        dry_run=False, limit_groups=2)
    assert stats["packages_created"] > 0

    from app.modules.maintenance_config.models import PMSchedule
    schedules = PMSchedule.query.all()
    assert len(schedules) == stats["packages_created"]
    # Every single one must have captured a work description -- this is
    # the actual reported gap ("work description not included").
    assert all(s.work_description_template for s in schedules)


def test_real_import_creates_correct_km_and_day_intervals(db):
    stats = import_pm_task_list(
        "/mnt/user-data/uploads/PM_Task_List.xlsx",
        dry_run=False, limit_groups=1)
    assert stats["packages_created"] > 0

    from app.modules.maintenance_config.models import PMSchedule
    first_service = PMSchedule.query.filter_by(interval_km=1000).first()
    assert first_service is not None
    assert first_service.interval_days == 30  # 1MTH -> 30 days, as requested
    assert "First 1,000" in first_service.work_description_template \
        or "First 1000" in first_service.work_description_template


def test_reset_pm_data_clears_all_three_tables_in_fk_safe_order(db):
    from import_pm_task_list import reset_pm_data
    import_pm_task_list("/mnt/user-data/uploads/PM_Task_List.xlsx",
                        dry_run=False, limit_groups=2)

    from app.modules.maintenance_config.models import (
        PMSchedule, PMScopeTemplate, PMScopeItem)
    assert PMSchedule.query.count() > 0
    assert PMScopeTemplate.query.count() > 0
    assert PMScopeItem.query.count() > 0

    deleted = reset_pm_data()
    assert deleted["pm_schedules"] > 0
    assert deleted["pm_scope_templates"] > 0
    assert deleted["pm_scope_items"] > 0

    assert PMSchedule.query.count() == 0
    assert PMScopeTemplate.query.count() == 0
    assert PMScopeItem.query.count() == 0


def test_reset_then_reimport_produces_a_clean_result(db):
    """The actual workflow requested: reset, then re-run the import, and
    end up with exactly the fresh import's data -- no leftover
    duplicates from before the reset."""
    from import_pm_task_list import reset_pm_data
    import_pm_task_list("/mnt/user-data/uploads/PM_Task_List.xlsx",
                        dry_run=False, limit_groups=1)
    reset_pm_data()
    stats = import_pm_task_list("/mnt/user-data/uploads/PM_Task_List.xlsx",
                                dry_run=False, limit_groups=1)

    from app.modules.maintenance_config.models import PMSchedule
    assert PMSchedule.query.count() == stats["packages_created"]


def test_reset_detaches_external_references_instead_of_failing(db):
    """Regression test for a real production error: a Vehicle's
    'Assigned PM Template' (pm_schedule_id) and a real MaintenanceOrder's
    pm_schedule_id/scope_template_id all reference these tables by
    foreign key. On MySQL (unlike SQLite's more lenient default), a bulk
    DELETE against a still-referenced pm_scope_templates row fails
    outright with 'Cannot delete or update a parent row: a foreign key
    constraint fails'. reset_pm_data() must detach (not fail on) these
    external references, and must NOT delete the Vehicle/MaintenanceOrder
    rows themselves -- only null the one stale column each."""
    from datetime import date
    from import_pm_task_list import reset_pm_data
    from app.cli import _seed_transaction_types
    from app.modules.maintenance_config.models import PMSchedule, PMScopeTemplate
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder, TransactionType)

    _seed_transaction_types()
    db.session.commit()
    import_pm_task_list("/mnt/user-data/uploads/PM_Task_List.xlsx",
                        dry_run=False, limit_groups=1)
    sched = PMSchedule.query.first()
    tpl = PMScopeTemplate.query.first()

    branch = BranchService().create(code="BR-RESETTEST", name="Reset Test Branch")
    vtype = VehicleTypeService().create(code="LV-RESETTEST", name="Light",
                                        category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vtype.id, brand="Toyota", model="Hilux", year=2020,
        branch_id=branch.id, conduction_number="RESET-000")
    vehicle.pm_schedule_id = sched.id
    tt = TransactionType.query.filter_by(code="ADM-MIGRATE-EXPENSE").first()
    mo = MaintenanceOrder(
        vehicle_id=vehicle.id, order_category="OPERATIONAL",
        transaction_type_id=tt.id, scheduled_date=date.today(),
        status="DRAFT", pm_schedule_id=sched.id, scope_template_id=tpl.id)
    db.session.add(mo)
    db.session.commit()
    mo_id, vehicle_id = mo.id, vehicle.id

    # Must not raise (this is exactly what raised IntegrityError before
    # the fix).
    deleted = reset_pm_data()
    assert deleted["vehicles_unlinked"] >= 1
    assert deleted["maintenance_orders_unlinked"] >= 1

    # The referencing rows themselves must still exist -- only the
    # column pointing at the now-deleted template is cleared.
    surviving_vehicle = db.session.get(Vehicle, vehicle_id)
    surviving_mo = db.session.get(MaintenanceOrder, mo_id)
    assert surviving_vehicle is not None
    assert surviving_mo is not None
    assert surviving_vehicle.pm_schedule_id is None
    assert surviving_mo.pm_schedule_id is None
    assert surviving_mo.scope_template_id is None

    assert PMSchedule.query.count() == 0
    assert PMScopeTemplate.query.count() == 0
