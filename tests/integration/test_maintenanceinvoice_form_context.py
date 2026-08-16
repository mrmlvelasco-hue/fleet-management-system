"""MO and Vehicle context on the invoice ENTRY form.

The detail page already carries this. The form where the invoice is
actually typed did not -- it showed a single subtitle line with the
order number and the plate, and nothing else. That is the screen where
the context matters most: someone is holding a vendor's paper invoice
and keying it in, and needs to confirm they are attaching it to the
right order and the right vehicle BEFORE saving, not after.
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
def order(app, db, admin):
    from app.modules.master_data.reference.service import (
        VehicleTypeService, MaintenanceTypeService)
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    vt = VehicleTypeService().create(code="VT-IF", name="Truck",
                                     category="HEAVY")
    mt = MaintenanceTypeService().create(code="MT-IF", name="PMS",
                                         category="PREVENTIVE")
    branch = BranchService().create(code="BR-IF", name="Carmona Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, branch_id=branch.id, brand="Mitsubishi",
        model="Canter", year=2018, plate_number="TIL-250",
        conduction_number="CN-IF1")

    mo = MaintenanceOrder(
        document_number="MO-2026-000012", vehicle_id=vehicle.id,
        maintenance_type_id=mt.id, order_category="MAINTENANCE",
        category="PREVENTIVE", scheduled_date=date(2026, 6, 2),
        completed_date=date(2026, 6, 4), odometer_at_service=132400,
        description="Replace clutch assembly", status="COMPLETED",
        requested_by=admin.id)
    db.session.add(mo)
    db.session.commit()
    return mo


def _login(client):
    return client.post("/login", data={"username": "admin",
                                       "password": "Testpass123!"},
                       follow_redirects=True)


def _form(client, order):
    return client.get(
        f"/transactions/maintenance-orders/{order.id}/invoices/new"
    ).get_data(as_text=True)


def test_form_opens(app, client, db, order):
    _login(client)
    resp = client.get(
        f"/transactions/maintenance-orders/{order.id}/invoices/new")
    assert resp.status_code == 200


def test_form_shows_the_order_number(app, client, db, order):
    _login(client)
    assert "MO-2026-000012" in _form(client, order)


def test_form_shows_the_order_dates_and_odometer(app, client, db, order):
    _login(client)
    html = _form(client, order)
    assert "2026-06-02" in html
    assert "2026-06-04" in html
    assert "132,400" in html or "132400" in html


def test_form_shows_the_work_description(app, client, db, order):
    """What the vendor is billing for -- the thing to check the invoice
    against before saving."""
    _login(client)
    assert "Replace clutch assembly" in _form(client, order)


def test_form_shows_the_vehicle_plate(app, client, db, order):
    _login(client)
    assert "TIL-250" in _form(client, order)


def test_form_shows_the_vehicle_conduction_number(app, client, db, order):
    _login(client)
    assert "CN-IF1" in _form(client, order)


def test_form_shows_the_vehicle_make_and_model(app, client, db, order):
    _login(client)
    html = _form(client, order)
    assert "Mitsubishi" in html and "Canter" in html


def test_form_shows_the_branch(app, client, db, order):
    _login(client)
    assert "Carmona Branch" in _form(client, order)


def test_form_still_has_its_input_fields(app, client, db, order):
    """The added context must not have displaced the actual form."""
    _login(client)
    html = _form(client, order)
    for field in ("invoice_number", "invoice_date", "or_number",
                  "po_number", "dr_number"):
        assert f'name="{field}"' in html, f"{field} input missing"
