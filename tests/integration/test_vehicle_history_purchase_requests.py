"""Purchase Requests belong to the vehicle's history.

When a Maintenance Order is approved, the parts list becomes a draft
Purchase Request automatically. That PR is part of what happened to the
vehicle -- money committed against it -- so it belongs on the vehicle's
timeline alongside the order that produced it.

The link is held on the ORDER (maintenance_orders.purchase_request_id),
not on the PR: PurchaseRequest has no vehicle_id at all. So the history
reaches PRs by walking Vehicle -> MaintenanceOrder -> PurchaseRequest.
That covers every PR generated from an order, which is the case that
matters here. A PR raised standalone has no vehicle on it anywhere and
cannot appear until one is added -- deliberately out of scope.
"""
import pytest
from datetime import date

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def admin(app, db):
    from app.modules.user_management.models import User
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    return User.query.filter_by(username="admin").first()


@pytest.fixture()
def env(app, db, admin):
    from app.modules.master_data.reference.service import (
        VehicleTypeService, MaintenanceTypeService)
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest, PurchaseRequestLine)

    vt = VehicleTypeService().create(code="VT-PRH", name="Truck",
                                     category="HEAVY")
    mt = MaintenanceTypeService().create(code="MT-PRH", name="PMS",
                                         category="PREVENTIVE")
    branch = BranchService().create(code="BR-PRH", name="Cavite")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, branch_id=branch.id, brand="Isuzu",
        model="Elf", year=2019, plate_number="PRH-1234",
        conduction_number="CN-PRH1", acquisition_date=date(2019, 1, 10))

    mo = MaintenanceOrder(
        document_number="MO-2026-000100", vehicle_id=vehicle.id,
        maintenance_type_id=mt.id, order_category="MAINTENANCE",
        category="PREVENTIVE", scheduled_date=date(2026, 4, 1),
        completed_date=date(2026, 4, 3), odometer_at_service=51000,
        description="Change oil", status="COMPLETED",
        requested_by=admin.id)
    db.session.add(mo)
    db.session.flush()

    pr = PurchaseRequest(
        document_number="PR-2026-000055",
        description="Parts for MO-2026-000100 — PRH-1234",
        amount=4520, status="APPROVED", requested_by=admin.id,
        needed_by_date=date(2026, 4, 2))
    db.session.add(pr)
    db.session.flush()
    pr.lines.append(PurchaseRequestLine(
        item_description="4L Oil (P/N OIL0001) - 5W40 [GAL]",
        quantity=1, unit_cost=4520, line_total=4520))
    mo.purchase_request_id = pr.id
    db.session.commit()
    return {"vehicle": vehicle, "mo": mo, "pr": pr}


def _svc():
    from app.core.vehicle_activity_history_service import (
        VehicleActivityHistoryService)
    return VehicleActivityHistoryService()


def _rows(vehicle):
    return _svc().get_activity_rows(vehicle)


# ── The PR appears on the timeline ──────────────────────────────────

def test_purchase_request_appears_in_the_vehicle_history(app, db, env):
    types = [r["activity_type"] for r in _rows(env["vehicle"])]
    assert "Purchase Request" in types


def test_the_row_carries_the_pr_document_number(app, db, env):
    row = next(r for r in _rows(env["vehicle"])
               if r["activity_type"] == "Purchase Request")
    assert "PR-2026-000055" in row["description"]


def test_the_row_carries_the_amount_committed(app, db, env):
    row = next(r for r in _rows(env["vehicle"])
               if r["activity_type"] == "Purchase Request")
    assert row["cost"] is not None
    assert float(row["cost"]) == 4520.0


def test_the_row_names_the_order_it_came_from(app, db, env):
    """Reading the timeline, "why was this raised" has to be answerable
    without opening the PR."""
    row = next(r for r in _rows(env["vehicle"])
               if r["activity_type"] == "Purchase Request")
    assert "MO-2026-000100" in row["description"]


def test_history_stays_in_date_order_with_the_pr_included(app, db, env):
    rows = _rows(env["vehicle"])
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)


# ── Not inventing rows ──────────────────────────────────────────────

def test_an_order_with_no_pr_adds_no_purchase_request_row(app, db, env):
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    mo = db.session.get(MaintenanceOrder, env["mo"].id)
    mo.purchase_request_id = None
    db.session.commit()
    types = [r["activity_type"] for r in _rows(env["vehicle"])]
    assert "Purchase Request" not in types


def test_another_vehicles_pr_does_not_leak_in(app, db, admin, env):
    """The walk is Vehicle -> its own orders -> their PRs, so a PR
    belonging to a different vehicle must never appear here."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest)

    vt2 = VehicleTypeService().create(code="VT-PRH2", name="Van",
                                      category="LIGHT")
    br2 = BranchService().create(code="BR-PRH2", name="Naic")
    other = VehicleService().create(
        vehicle_type_id=vt2.id, branch_id=br2.id, brand="Toyota",
        model="Hiace", year=2021, plate_number="OTH-9999",
        conduction_number="CN-OTH1")
    other_pr = PurchaseRequest(document_number="PR-2026-000999",
                               description="Someone else's parts",
                               amount=1, status="DRAFT",
                               requested_by=admin.id)
    db.session.add(other_pr)
    db.session.flush()
    other_mo = MaintenanceOrder(
        document_number="MO-2026-000999", vehicle_id=other.id,
        order_category="MAINTENANCE", scheduled_date=date(2026, 4, 1),
        completed_date=date(2026, 4, 2), status="COMPLETED",
        requested_by=admin.id, purchase_request_id=other_pr.id)
    db.session.add(other_mo)
    db.session.commit()

    descriptions = " ".join(r["description"] or ""
                            for r in _rows(env["vehicle"]))
    assert "PR-2026-000999" not in descriptions


def test_a_pr_on_an_incomplete_order_still_shows(app, db, env):
    """A PR is raised at APPROVAL, long before the work is finished.
    The rest of the timeline only shows COMPLETED activity, but holding
    the PR back until completion would hide committed money for as long
    as the job takes -- which is precisely when someone is asking where
    the parts are.
    """
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    mo = db.session.get(MaintenanceOrder, env["mo"].id)
    mo.status = "IN_PROGRESS"
    mo.completed_date = None
    db.session.commit()
    types = [r["activity_type"] for r in _rows(env["vehicle"])]
    assert "Purchase Request" in types


# ── It reaches the screens ──────────────────────────────────────────

def _login(client):
    return client.post("/login", data={"username": "admin",
                                       "password": "Testpass123!"},
                       follow_redirects=True)


def test_pr_shows_on_the_vehicle_profile_print(app, client, db, env):
    _login(client)
    html = client.get(
        f"/master/vehicles/{env['vehicle'].id}/print"
    ).get_data(as_text=True)
    assert "PR-2026-000055" in html


def test_pr_shows_on_the_activity_history_report(app, client, db, env):
    _login(client)
    html = client.get(
        "/master/reports/vehicle-activity-history"
        f"?vehicle_ids={env['vehicle'].id}").get_data(as_text=True)
    assert "PR-2026-000055" in html

