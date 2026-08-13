"""The standard transaction list filter.

Every transaction list previously loaded its whole table and offered
only DataTables' single client-side search box. list_filtered() gives
them one shared server-side filter set (search, status, branch, date
range) plus pagination, built on the same _visible_query() that list()
uses so a filtered listing can never show something the unfiltered one
would have hidden.
"""
from datetime import date, timedelta

import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def orders(app, db):
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.user_management.models import User

    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    admin = User.query.filter_by(username="admin").first()

    branch = BranchService().create(code="BR-FLT", name="Filter Branch")
    other = BranchService().create(code="BR-OTH", name="Other Branch")
    vt = VehicleTypeService().create(code="LV-FLT", name="Light",
                                     category="LIGHT")
    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2022,
                                 branch_id=branch.id,
                                 conduction_number="FLT-1",
                                 plate_number="ABC-111")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Vios", year=2022,
                                 branch_id=other.id,
                                 conduction_number="FLT-2",
                                 plate_number="XYZ-999")
    made = []
    for i in range(40):
        mo = MaintenanceOrder(
            vehicle_id=(v1.id if i < 25 else v2.id),
            order_category="MAINTENANCE",
            status=("APPROVED" if i % 2 else "DRAFT"),
            document_number=f"MO-TEST-{i:03d}",
            scheduled_date=date.today() - timedelta(days=i),
            requested_by=admin.id)
        made.append(mo)
    db.session.add_all(made)
    db.session.commit()
    return {"branch": branch, "other": other, "admin": admin}


def _svc():
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    return MaintenanceOrderService()


def test_results_are_paginated_not_all_returned(app, db, orders):
    rows, pagination = _svc().list_filtered(page=1, per_page=25)
    assert len(rows) == 25
    assert pagination.total >= 40
    assert pagination.pages >= 2


def test_second_page_returns_different_records(app, db, orders):
    p1, _ = _svc().list_filtered(page=1, per_page=25)
    p2, _ = _svc().list_filtered(page=2, per_page=25)
    assert not ({r.id for r in p1} & {r.id for r in p2})


def test_status_filter_narrows_the_total(app, db, orders):
    _rows, everything = _svc().list_filtered(page=1)
    rows, filtered = _svc().list_filtered(page=1, status="APPROVED")
    assert filtered.total < everything.total
    assert all(r.status == "APPROVED" for r in rows)


def test_search_matches_the_document_number(app, db, orders):
    rows, pagination = _svc().list_filtered(page=1, search="MO-TEST-007")
    assert pagination.total == 1
    assert rows[0].document_number == "MO-TEST-007"


def test_search_also_matches_the_vehicle_plate(app, db, orders):
    """People searching a transaction list usually have a plate in hand,
    not a document number."""
    rows, pagination = _svc().list_filtered(page=1, search="XYZ-999")
    assert pagination.total == 15
    assert all(r.vehicle.plate_number == "XYZ-999" for r in rows)


def test_branch_filter_uses_the_vehicles_branch(app, db, orders):
    """A Maintenance Order has no branch of its own -- it inherits one
    through its vehicle, and the filter has to follow that."""
    rows, pagination = _svc().list_filtered(
        page=1, branch_id=orders["branch"].id)
    assert pagination.total == 25
    assert all(r.vehicle.branch_id == orders["branch"].id for r in rows)


def test_date_range_is_inclusive_of_the_end_day(app, db, orders):
    today = date.today()
    rows, pagination = _svc().list_filtered(
        page=1, date_from=today, date_to=today)
    assert pagination.total == 1, (
        "an end date must include that whole day, not stop at its start")


def test_filters_combine_as_and_not_or(app, db, orders):
    _r, by_status = _svc().list_filtered(page=1, status="APPROVED")
    _r2, by_branch = _svc().list_filtered(page=1,
                                          branch_id=orders["branch"].id)
    rows, both = _svc().list_filtered(page=1, status="APPROVED",
                                      branch_id=orders["branch"].id)
    assert both.total <= min(by_status.total, by_branch.total)
    assert all(r.status == "APPROVED"
              and r.vehicle.branch_id == orders["branch"].id for r in rows)


def test_a_filter_the_model_does_not_support_is_ignored_not_fatal(
        app, db, orders):
    """The shared filter bar is used by several modules whose models
    genuinely differ. Asking for a field a model lacks must not break
    that screen."""
    from app.modules.transactions.purchase_request.service import (
        PurchaseRequestService)
    # PurchaseRequest has no vehicle_id; a plate search must simply
    # return nothing rather than raise.
    rows, pagination = PurchaseRequestService().list_filtered(
        page=1, search="ABC-111")
    assert isinstance(rows, list)


def test_status_choices_come_from_real_data(app, db, orders):
    choices = _svc().status_choices()
    assert "APPROVED" in choices
    assert "DRAFT" in choices


def test_filtering_still_respects_visibility(app, db, orders):
    """The filtered listing must not become a way around org scoping."""
    from app.modules.user_management.models import User
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    from app.core.security.password import hash_password

    scoped = User(username="scoped", email="s@x.com",
                  password_hash=hash_password("Testpass123!"),
                  must_change_password=False)
    db.session.add(scoped)
    db.session.commit()
    UserOrgScopeService().assign(scoped.id, scope_type="BRANCH",
                                 branch_id=orders["branch"].id)
    db.session.commit()

    rows, pagination = _svc().list_filtered(page=1, user=scoped,
                                            per_page=100)
    assert pagination.total == 25, "scoped user saw another branch's orders"
    assert all(r.vehicle.branch_id == orders["branch"].id for r in rows)


def test_mo_list_page_renders_the_filter_bar_and_pager(app, db, orders):
    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"},
                follow_redirects=True)
    html = client.get("/transactions/maintenance-orders").get_data(as_text=True)
    assert 'name="q"' in html
    assert 'name="status"' in html
    assert 'name="branch_id"' in html
    assert 'name="date_from"' in html
    assert 'name="maintenance_class"' in html   # module-specific filter
    assert "pagination" in html
