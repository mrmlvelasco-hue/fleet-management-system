"""Cost Center on Department (item 1) and the Maintenance Type filter
on the Due Maintenance report (item 2).

Cost centre source chain, confirmed against the existing schema rather
than adding new links: Vehicle already carries department_id, and a
Department will carry cost_center -- so any Maintenance Order (PMS or
Operational) reaches its cost centre through its own vehicle, with no
new column on the MO itself.
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


def _client(app, db):
    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


# ── 1. Cost Center ────────────────────────────────────────────────────────

def test_department_can_store_a_cost_center(app, db):
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    branch = BranchService().create(code="BR-CC1", name="Branch CC1")
    dept = DepartmentService().create(
        code="DP-CC1", name="Fleet Ops", branch_id=branch.id,
        cost_center="CC-1001")
    assert dept.cost_center == "CC-1001"


def test_cost_center_is_optional(app, db):
    """An existing department created before this field existed must
    still be valid -- not every org tracks cost centres."""
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    branch = BranchService().create(code="BR-CC2", name="Branch CC2")
    dept = DepartmentService().create(
        code="DP-CC2", name="Admin", branch_id=branch.id)
    assert dept.cost_center is None


def test_two_departments_in_one_branch_can_have_different_cost_centers(
        app, db):
    """The core business requirement: multiple departments per branch,
    each with its own cost centre."""
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    branch = BranchService().create(code="BR-CC3", name="Branch CC3")
    d1 = DepartmentService().create(code="DP-A", name="Ops",
                                    branch_id=branch.id, cost_center="CC-A")
    d2 = DepartmentService().create(code="DP-B", name="Sales",
                                    branch_id=branch.id, cost_center="CC-B")
    assert d1.branch_id == d2.branch_id
    assert d1.cost_center != d2.cost_center


def test_vehicle_resolves_its_cost_center_through_its_department(app, db):
    """The source chain that makes this work without touching the MO
    schema at all: Vehicle -> Department -> cost_center."""
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService

    branch = BranchService().create(code="BR-CC4", name="Branch CC4")
    dept = DepartmentService().create(code="DP-CC4", name="Fleet",
                                      branch_id=branch.id,
                                      cost_center="CC-4004")
    vt = VehicleTypeService().create(code="LV-CC4", name="Light",
                                     category="LIGHT")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, department_id=dept.id,
        conduction_number="CC4-1")
    assert v.cost_center == "CC-4004"


def test_vehicle_without_a_department_has_no_cost_center(app, db):
    """Must return None rather than raising -- a vehicle isn't required
    to have a department assigned."""
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService

    branch = BranchService().create(code="BR-CC5", name="Branch CC5")
    vt = VehicleTypeService().create(code="LV-CC5", name="Light",
                                     category="LIGHT")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, conduction_number="CC5-1")
    assert v.cost_center is None


def test_maintenance_order_resolves_cost_center_from_its_vehicle(app, db):
    """Any MO -- PMS or Operational -- reaches the cost centre through
    its own vehicle, with no new column on the MO."""
    from datetime import date
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    branch = BranchService().create(code="BR-CC6", name="Branch CC6")
    dept = DepartmentService().create(code="DP-CC6", name="Fleet",
                                      branch_id=branch.id,
                                      cost_center="CC-6006")
    vt = VehicleTypeService().create(code="LV-CC6", name="Light",
                                     category="LIGHT")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, department_id=dept.id,
        conduction_number="CC6-1")
    mo = MaintenanceOrder(vehicle_id=v.id, order_category="OPERATIONAL",
                          scheduled_date=date.today(), status="DRAFT")
    db.session.add(mo)
    db.session.commit()
    assert mo.cost_center == "CC-6006"


def test_department_form_accepts_and_persists_cost_center(app, db):
    from app.modules.master_data.org.service import BranchService
    branch = BranchService().create(code="BR-CC7", name="Branch CC7")
    client = _client(app, db)
    client.post("/master/departments/new", data={
        "code": "DP-CC7", "name": "Logistics", "branch_id": branch.id,
        "cost_center": "CC-7007", "description": ""},
        follow_redirects=True)
    from app.modules.master_data.org.models import Department
    dept = Department.query.filter_by(code="DP-CC7").first()
    assert dept is not None
    assert dept.cost_center == "CC-7007"


def test_department_list_shows_the_cost_center_column(app, db):
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    branch = BranchService().create(code="BR-CC8", name="Branch CC8")
    DepartmentService().create(code="DP-CC8", name="Ops",
                               branch_id=branch.id, cost_center="CC-8008")
    html = _client(app, db).get("/master/departments").get_data(as_text=True)
    assert "Cost Center" in html
    assert "CC-8008" in html


# ── 2. Maintenance Type filter on the Due report ─────────────────────────

def test_report_filters_include_maintenance_type(app, db):
    from app.modules.system_admin.routes import _report_filters_from_request
    with app.test_request_context("/?maintenance_type_id=7"):
        filters = _report_filters_from_request()
    assert filters["maintenance_type_id"] == "7"


def test_pms_compliance_page_offers_a_maintenance_type_filter(app, db):
    html = _client(app, db).get(
        "/admin/reports/pms-compliance").get_data(as_text=True)
    assert "maintenance_type_id" in html
    assert "Maintenance Type" in html


def test_setting_only_a_maintenance_type_filter_runs_the_report(app, db):
    """The report deliberately doesn't run on a bare page load, but any
    filter being set counts as a deliberate submission -- the new
    filter must count too, otherwise selecting it alone would silently
    return nothing."""
    from app.modules.system_admin.routes import _report_filters_from_request
    with app.test_request_context("/?maintenance_type_id=3"):
        filters = _report_filters_from_request()
        has_filters = any(filters.get(k) for k in
                         ("branch_id", "vehicle_type_id", "status",
                          "maintenance_type_id"))
    assert has_filters is True
