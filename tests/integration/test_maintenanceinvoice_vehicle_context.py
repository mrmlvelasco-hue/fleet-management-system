"""Maintenance Order and Vehicle context on the Invoice.

An invoice arrives from a vendor referencing work done on a specific
vehicle. Reading it, the reference numbers people need in hand are the
vendor's own invoice number, the Maintenance Order it settles, and the
vehicle that was worked on -- plate or conduction number above all,
since that is what everyone on the ground calls a vehicle by.

Previously the invoice detail showed only a small link carrying the MO
number, so identifying the vehicle meant opening the order in another
tab and reading it there.
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
    """A vehicle, an order against it, a vendor, and an invoice."""
    from app.modules.master_data.reference.service import (
        VehicleTypeService, MaintenanceTypeService)
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.vendor.models import Vendor
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.maintenance_invoice.models import (
        MaintenanceInvoice)

    vt = VehicleTypeService().create(code="VT-INV", name="Truck",
                                     category="HEAVY")
    mt = MaintenanceTypeService().create(code="MT-INV", name="PMS",
                                         category="PREVENTIVE")
    branch = BranchService().create(code="BR-INV", name="Cavite Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, branch_id=branch.id, brand="Isuzu",
        model="Elf", year=2019, plate_number="XYZ-9876",
        conduction_number="CN-INV1")

    vendor = Vendor(code="V-INV", name="Reliable Motors")
    db.session.add(vendor)
    db.session.flush()

    mo = MaintenanceOrder(
        document_number="MO-2026-000042", vehicle_id=vehicle.id,
        maintenance_type_id=mt.id, order_category="MAINTENANCE",
        category="PREVENTIVE", scheduled_date=date(2026, 5, 4),
        completed_date=date(2026, 5, 6), odometer_at_service=84210,
        description="Change oil and brake pads", status="COMPLETED",
        requested_by=admin.id)
    db.session.add(mo)
    db.session.flush()

    inv = MaintenanceInvoice(
        document_number="INV-2026-000007", maintenance_order_id=mo.id,
        vendor_id=vendor.id, invoice_number="SI-55123",
        invoice_date=date(2026, 5, 7), status="DRAFT",
        requested_by=admin.id)
    db.session.add(inv)
    db.session.commit()
    return {"vehicle": vehicle, "mo": mo, "invoice": inv,
            "vendor": vendor, "branch": branch}


def _login(client):
    return client.post("/login", data={"username": "admin",
                                       "password": "Testpass123!"},
                       follow_redirects=True)


def _detail(client, env):
    return client.get(
        f"/transactions/invoices/{env['invoice'].id}"
    ).get_data(as_text=True)


# ── The order this invoice settles ──────────────────────────────────

def test_invoice_shows_the_maintenance_order_number(app, client, db, env):
    _login(client)
    assert "MO-2026-000042" in _detail(client, env)


def test_invoice_shows_the_orders_dates_and_odometer(app, client, db, env):
    """What was done and when -- enough to sanity-check the charge
    without opening the order in another tab."""
    html = (_login(client), _detail(client, env))[1]
    assert "2026-05-04" in html          # scheduled
    assert "2026-05-06" in html          # completed
    assert "84,210" in html or "84210" in html


def test_invoice_shows_the_order_description(app, client, db, env):
    _login(client)
    assert "Change oil and brake pads" in _detail(client, env)


# ── The vehicle the work was done on ────────────────────────────────

def test_invoice_shows_the_vehicle_plate_number(app, client, db, env):
    """The single most important reference: the plate is what everyone
    actually calls a vehicle by."""
    _login(client)
    assert "XYZ-9876" in _detail(client, env)


def test_invoice_shows_the_vehicle_conduction_number(app, client, db, env):
    """A brand-new unit may have no plate yet -- Philippine LTO issues
    the conduction number first -- so both have to be reachable."""
    _login(client)
    assert "CN-INV1" in _detail(client, env)


def test_invoice_shows_the_vehicle_make_and_model(app, client, db, env):
    _login(client)
    html = _detail(client, env)
    assert "Isuzu" in html and "Elf" in html


def test_invoice_shows_the_vehicles_branch(app, client, db, env):
    """Which outlet owns the cost."""
    _login(client)
    assert "Cavite Branch" in _detail(client, env)


def test_vehicle_links_through_to_its_record(app, client, db, env):
    _login(client)
    assert f"/vehicles/{env['vehicle'].id}" in _detail(client, env)


# ── Degrading safely ────────────────────────────────────────────────

def test_template_guards_the_order_and_vehicle_relationships(app):
    """MaintenanceOrder.vehicle_id is NOT NULL at the database level, so
    the "missing vehicle" case cannot be set up through the ORM at all --
    the insert is rejected before any page renders. That makes a
    behavioural test impossible here, but the concern is still real: the
    constraint could be relaxed later, and a detail page that 500s on
    unexpected data is worse than one showing a dash. Asserting the
    guards exist is the honest amount of coverage available.
    """
    from pathlib import Path
    tpl = Path(app.root_path, "modules", "transactions", "templates",
               "transactions", "maintenanceinvoice_detail.html").read_text()
    assert "{% set veh = mo.vehicle if mo else None %}" in tpl
    assert "{% if veh %}" in tpl
    assert "{% if mo %}" in tpl


def test_invoice_still_shows_its_own_reference_numbers(app, client, db, env):
    """The vendor's own numbers stay primary -- the added context must
    not have displaced them."""
    _login(client)
    html = _detail(client, env)
    assert "SI-55123" in html
    assert "INV-2026-000007" in html
