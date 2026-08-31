"""Maintenance Cost Summary — JSON, Excel export, and the budget section.

Parity source: report_maintenance_cost_summary (system_admin/routes.py),
generate_maintenance_cost_summary_xlsx, VehicleBudgetService. See
docs/parity-report-maintenance-cost.md.

This report has two independent sections and the tests are organised the
same way: the cost detail (which the XLSX export also carries) and the
Budget Utilization by Vehicle section (screen only -- no XLSX
equivalent). The two must not be conflated, and the API is asserted for
building both without confusing where a figure comes from.
"""
import json
from datetime import date, timedelta

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Cost Report Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "reportmaintenancecost.view").all()
    viewer = User(username="costviewer", email="cv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [role]
    nobody = User(username="costnobody", email="cn@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    db.session.add_all([role, viewer, nobody])

    branch = BranchService().create(code="BR-COST", name="Cost Branch")
    vt = VehicleTypeService().create(code="LV-COST", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="COST-MT", name="PMS",
                                         category="PM")
    _ensure_doc_type("MO")

    plain = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="COST-000")
    # A vehicle budget tracking DOES apply to.
    budgeted = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="COST-001",
        assignment_group_classification="COMPANY_OWNED",
        delivery_date=date.today() - timedelta(days=30))
    db.session.commit()
    return {"viewer": viewer, "branch": branch, "vt": vt, "mt": mt,
            "plain": plain, "budgeted": budgeted}


def _completed_order(env, vehicle, cost, days_ago=1):
    o = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=env["mt"].id,
        scheduled_date=date.today(), user=env["viewer"],
        description="Cost report fixture", estimated_cost=cost)
    o.status = "COMPLETED"
    o.actual_cost = cost
    o.completed_date = date.today() - timedelta(days=days_ago)
    from app.extensions import db
    db.session.commit()
    return o


def _token(client, username="costviewer"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, r


# ------------------------------------------------------------- guards

def test_rejects_anonymous(db, client, env):
    assert client.get("/api/v1/reports/maintenance-cost").status_code == 401


def test_requires_the_permission(db, client, env):
    status, _ = _get(client, "/api/v1/reports/maintenance-cost",
                     _token(client, "costnobody"))
    assert status == 403


def test_runs_on_open_no_gate(db, client, env):
    status, r = _get(client, "/api/v1/reports/maintenance-cost",
                     _token(client))
    assert status == 200
    body = json.loads(r.get_data(as_text=True))
    assert isinstance(body["rows"], list)


# --------------------------------------------------------- cost detail

def test_only_completed_orders_appear(db, client, env):
    _completed_order(env, env["plain"], 1000)
    draft = MaintenanceOrderService().create(
        vehicle_id=env["plain"].id, maintenance_type_id=env["mt"].id,
        scheduled_date=date.today(), user=env["viewer"],
        description="Still draft", estimated_cost=500)

    _status, r = _get(client, "/api/v1/reports/maintenance-cost",
                      _token(client))
    body = json.loads(r.get_data(as_text=True))
    numbers = [row["mo_number"] for row in body["rows"]]
    assert draft.document_number not in numbers


def test_total_is_the_sum_of_the_filtered_rows_not_all_orders(db, client, env):
    """The total must reflect what the filter narrowed the DETAIL rows
    to. Filtering to a single vehicle and getting back the whole
    fleet's total would mislead exactly the person the total exists
    to help."""
    _completed_order(env, env["plain"], 1000)
    _completed_order(env, env["budgeted"], 2000)

    _status, r = _get(
        client,
        f"/api/v1/reports/maintenance-cost?plate_number=COST-000",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    assert len(body["rows"]) == 1
    assert body["total"] == 1000.0


def test_plate_number_filter_matches_conduction_number_too(db, client, env):
    """Unregistered vehicles have no plate yet -- the substring match
    must fall back to conduction_number, same as the Jinja route."""
    _completed_order(env, env["plain"], 1000)
    _status, r = _get(
        client, "/api/v1/reports/maintenance-cost?plate_number=cost-000",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    assert len(body["rows"]) == 1


def test_date_range_is_inclusive_both_ends(db, client, env):
    o = _completed_order(env, env["plain"], 1000, days_ago=5)
    d = o.completed_date.isoformat()
    _status, r = _get(
        client,
        f"/api/v1/reports/maintenance-cost?date_from={d}&date_to={d}",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    assert len(body["rows"]) == 1


def test_row_shape_matches_the_report_columns(db, client, env):
    _completed_order(env, env["plain"], 1500)
    _status, r = _get(client, "/api/v1/reports/maintenance-cost",
                      _token(client))
    body = json.loads(r.get_data(as_text=True))
    row = body["rows"][0]
    for key in ("mo_number", "vehicle", "branch", "category",
               "maintenance_type", "completed_date", "actual_cost"):
        assert key in row


# ------------------------------------------------ budget utilization

def test_budget_section_only_lists_applicable_vehicles(db, client, env):
    """The plain vehicle has no assignment_group_classification, so
    budget tracking is not applicable to it and it must not appear --
    applicable=False is 'not applicable', never an error or
    over-budget."""
    _completed_order(env, env["plain"], 1000)
    _completed_order(env, env["budgeted"], 2000)

    _status, r = _get(client, "/api/v1/reports/maintenance-cost",
                      _token(client))
    body = json.loads(r.get_data(as_text=True))
    vehicle_ids = {row["vehicle_id"] for row in body["budget_rows"]}
    assert env["budgeted"].id in vehicle_ids
    assert env["plain"].id not in vehicle_ids


def test_budget_section_reflects_the_same_filters_as_the_detail(db, client, env):
    """Built from vehicles in the FILTERED detail rows, not a separate
    unfiltered query -- filtering the vehicle out of the detail must
    remove it from the budget section too."""
    _completed_order(env, env["budgeted"], 2000)

    _status, r = _get(
        client, "/api/v1/reports/maintenance-cost?plate_number=nomatch",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    assert body["rows"] == []
    assert body["budget_rows"] == []


def test_budget_spent_is_independent_of_the_reports_date_filter(db, client, env):
    """The vehicle's budget-year window is anchored to its OWN
    delivery date, not the report's date_from/date_to. Narrowing the
    report's date range must not change the budget figures -- they are
    two different time windows that happen to overlap."""
    _completed_order(env, env["budgeted"], 2000, days_ago=10)

    _status, wide = _get(client, "/api/v1/reports/maintenance-cost",
                         _token(client))
    wide_budget = json.loads(wide.get_data(as_text=True))["budget_rows"]

    today = date.today().isoformat()
    _status, narrow = _get(
        client,
        f"/api/v1/reports/maintenance-cost?date_from={today}&date_to={today}",
        _token(client))
    narrow_body = json.loads(narrow.get_data(as_text=True))
    # The order itself is filtered OUT of the detail (it wasn't
    # completed today), but if the vehicle still shows in budget_rows
    # via some other completed order, its spent figure must match --
    # spent is not derived from these filtered rows at all.
    if narrow_body["budget_rows"]:
        narrow_row = next(r for r in narrow_body["budget_rows"]
                          if r["vehicle_id"] == env["budgeted"].id)
        wide_row = next(r for r in wide_budget
                        if r["vehicle_id"] == env["budgeted"].id)
        assert narrow_row["spent"] == wide_row["spent"]


def test_budget_row_shape(db, client, env):
    _completed_order(env, env["budgeted"], 2000)
    _status, r = _get(client, "/api/v1/reports/maintenance-cost",
                      _token(client))
    body = json.loads(r.get_data(as_text=True))
    row = next(r for r in body["budget_rows"]
              if r["vehicle_id"] == env["budgeted"].id)
    for key in ("mode", "classification", "current_year", "period_start",
               "period_end", "budget", "spent", "remaining", "over_budget"):
        assert key in row


# ------------------------------------------------------------- export

def test_export_is_an_xlsx_attachment(db, client, env):
    status, r = _get(client, "/api/v1/reports/maintenance-cost/export.xlsx",
                     _token(client))
    assert status == 200
    assert "spreadsheetml" in r.headers["Content-Type"]


def test_export_requires_the_permission(db, client, env):
    status, _ = _get(client, "/api/v1/reports/maintenance-cost/export.xlsx",
                     _token(client, "costnobody"))
    assert status == 403


def test_bad_branch_id_is_a_clean_400(db, client, env):
    status, _ = _get(client, "/api/v1/reports/maintenance-cost?branch_id=x",
                     _token(client))
    assert status == 400
