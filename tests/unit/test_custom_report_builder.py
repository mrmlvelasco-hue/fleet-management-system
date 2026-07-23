"""Tests for the Custom Report Builder -- saving definitions, running
them safely, and delivering them by email.
"""
from unittest.mock import patch

import pytest

from app.core.reporting.report_builder import (
    DATA_SOURCES, run_report, to_xlsx, ReportBuilderError)
from app.modules.system_admin.services.custom_report_service import (
    CustomReportService)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def fleet(db):
    vt = VehicleTypeService().create(code="LV-CR", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="MNL-CR", name="Manila")
    for i in range(3):
        v = VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
            year=2020 + i, branch_id=branch.id,
            conduction_number=f"CR-{i}", plate_number=f"CR{i}")
        v.current_odometer = 10000 * (i + 1)
    db.session.commit()
    return vt, branch


# ── Engine safety ───────────────────────────────────────────────────────────

def test_unknown_data_source_is_rejected(db):
    """A source that isn't on the whitelist must be unreachable -- this
    is what stops a report reading users or the audit trail."""
    for bad in ("users", "audit_logs", "permissions"):
        with pytest.raises(ReportBuilderError):
            run_report(bad, ["anything"])


def test_unknown_columns_are_dropped(db, fleet):
    """Column keys are filtered against the whitelist, so a definition
    naming a non-existent (or sensitive) column can't smuggle it in."""
    with pytest.raises(ReportBuilderError):
        run_report("vehicles", ["password_hash", "secret_column"])


def test_filter_values_are_bound_not_interpolated(db, fleet):
    """An injection attempt in a filter VALUE must be treated as a
    search string, never executed."""
    from app.modules.master_data.vehicle.models import Vehicle
    before = Vehicle.query.count()
    result = run_report(
        "vehicles", ["plate_number"],
        filters=[{"field": "brand", "op": "contains",
                 "value": "'; DROP TABLE vehicles; --"}])
    assert result["row_count"] == 0          # matched nothing
    assert Vehicle.query.count() == before   # table intact


def test_row_limit_is_capped(db, fleet):
    result = run_report("vehicles", ["plate_number"], limit=10 ** 9)
    assert result["row_count"] <= 5000


def test_permission_is_enforced_per_data_source(db, fleet):
    """A saved report must never become a way around normal access."""
    class NoAccess:
        def has_permission(self, code):
            return False
    with pytest.raises(ReportBuilderError):
        run_report("vehicles", ["plate_number"], user=NoAccess())


# ── Querying ────────────────────────────────────────────────────────────────

def test_selects_columns_across_a_join(db, fleet):
    result = run_report("vehicles",
                        ["plate_number", "brand", "branch_name"])
    assert [c["label"] for c in result["columns"]] == [
        "Plate No.", "Brand", "Branch"]
    assert result["row_count"] == 3
    assert all(row[2] == "Manila" for row in result["rows"])


def test_filter_and_sort(db, fleet):
    result = run_report("vehicles", ["plate_number", "year"],
                        filters=[{"field": "year", "op": "gte",
                                 "value": 2021}],
                        sort_key="year", sort_dir="desc")
    assert result["row_count"] == 2
    assert [r[1] for r in result["rows"]] == [2022, 2021]


def test_contains_filter(db, fleet):
    result = run_report("vehicles", ["plate_number"],
                        filters=[{"field": "brand", "op": "contains",
                                 "value": "toyo"}])
    assert result["row_count"] == 3


def test_invalid_operator_is_rejected(db, fleet):
    with pytest.raises(ReportBuilderError):
        run_report("vehicles", ["plate_number"],
                   filters=[{"field": "brand", "op": "; DROP TABLE",
                            "value": "x"}])


def test_bad_date_value_gives_a_clear_message(db, fleet):
    with pytest.raises(ReportBuilderError) as exc:
        run_report("vehicles", ["plate_number"],
                   filters=[{"field": "acquisition_date", "op": "gte",
                            "value": "not-a-date"}])
    assert "valid date" in str(exc.value)


def test_excel_output_is_a_real_workbook(db, fleet):
    result = run_report("vehicles", ["plate_number", "brand"])
    data = to_xlsx(result, "Test Report")
    assert data[:2] == b"PK"  # xlsx is a zip container


# ── Saved reports ───────────────────────────────────────────────────────────

def test_create_run_and_export_a_saved_report(db, fleet):
    svc = CustomReportService()
    report = svc.create(
        name="Fleet by Branch", data_source="vehicles",
        fields=["plate_number", "brand", "branch_name"],
        filters=[{"field": "year", "op": "gte", "value": 2021}],
        sort_key="plate_number")
    assert report.id
    assert report.fields == ["plate_number", "brand", "branch_name"]

    result = svc.run(report)
    assert result["row_count"] == 2

    filename, data = svc.to_excel(report)
    assert filename.endswith(".xlsx")
    assert data[:2] == b"PK"


def test_saving_without_columns_is_rejected(db, fleet):
    with pytest.raises(ReportBuilderError):
        CustomReportService().create(name="Empty", data_source="vehicles",
                                     fields=[])


def test_saving_with_an_unknown_source_is_rejected(db, fleet):
    with pytest.raises(ReportBuilderError):
        CustomReportService().create(name="Bad", data_source="users",
                                     fields=["id"])


def test_saved_definition_is_revalidated_at_run_time(db, fleet):
    """A row edited directly in the database must not be able to widen
    what the report can read."""
    svc = CustomReportService()
    report = svc.create(name="Tampered", data_source="vehicles",
                        fields=["plate_number"])
    report.fields_json = '["plate_number", "password_hash"]'
    db.session.commit()
    result = svc.run(report)          # must not raise, must not include it
    assert [c["key"] for c in result["columns"]] == ["plate_number"]


# ── Email delivery ──────────────────────────────────────────────────────────

def test_email_queues_one_row_per_recipient(db, fleet):
    from app.modules.system_admin.models import EmailOutbox
    svc = CustomReportService()
    report = svc.create(name="Emailed", data_source="vehicles",
                        fields=["plate_number"])
    outcome = svc.email(report, recipients="a@x.com, b@y.com")
    assert outcome["queued"] == 2
    assert EmailOutbox.query.filter_by(event_code="CUSTOM_REPORT").count() == 2
    # Queued only -- nothing is sent inside the request.
    assert all(r.status == "PENDING" for r in EmailOutbox.query.all())


def test_email_without_recipients_is_rejected(db, fleet):
    svc = CustomReportService()
    report = svc.create(name="NoDest", data_source="vehicles",
                        fields=["plate_number"])
    with pytest.raises(ReportBuilderError):
        svc.email(report)


def test_drain_regenerates_the_report_and_attaches_it(db, fleet):
    """The workbook is built at SEND time, so a scheduled delivery
    carries current data rather than a stale snapshot."""
    from app.modules.system_admin.services.email_outbox_service import (
        EmailOutboxService)
    svc = CustomReportService()
    report = svc.create(name="Attached", data_source="vehicles",
                        fields=["plate_number", "brand"])
    svc.email(report, recipients="ops@example.com")

    with patch("app.modules.system_admin.services.email_config_service"
               ".EmailSenderService.send") as sender:
        stats = EmailOutboxService().send_pending()

    assert stats["sent"] == 1
    attachments = sender.call_args.kwargs["attach_files"]
    assert attachments[0]["filename"].endswith(".xlsx")
    assert attachments[0]["data"][:2] == b"PK"
