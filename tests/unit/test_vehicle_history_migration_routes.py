"""Vehicle History Migration -- the UI screens on top of the already-
tested import_service: template download, upload (dry-run + real),
batch listing, and undo, all through real routes.
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


def _client(app, db):
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    from app.modules.user_management.models import Role, Permission
    role = Role.query.filter_by(name="System Administrator").first()
    perm = Permission.query.filter_by(code="historymigration.import").first()
    if perm not in role.permissions:
        role.permissions.append(perm)
        db.session.commit()
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


def _unprivileged_client(app, db, extra_permission_codes=None):
    """A real logged-in user with NO roles/permissions by default --
    unlike the seeded admin, which is deliberately given every
    permission and so can never be used to test that a permission
    check actually blocks anything. Optionally grants a small,
    specific set of OTHER permissions, so a page that itself requires
    some baseline access (e.g. vehicle.view) can still be reached while
    testing that ONE particular permission is what's actually missing.
    """
    sync_permissions(); db.session.commit()
    from app.modules.user_management.models import User, Role, Permission
    from app.core.security.password import hash_password
    user = User(username="noperms", email="noperms@example.com",
               password_hash=hash_password("Testpass123!"),
               must_change_password=False)
    if extra_permission_codes:
        role = Role(name="Limited Test Role")
        role.permissions = Permission.query.filter(
            Permission.code.in_(extra_permission_codes)).all()
        db.session.add(role)
        user.roles.append(role)
    db.session.add(user)
    db.session.commit()
    c = app.test_client()
    c.post("/login", data={"username": "noperms",
                           "password": "Testpass123!"})
    return c


def test_page_requires_the_dedicated_permission(app, db):
    """Confirmed separately from fuel.view/fuel.create -- a user without
    historymigration.import must not reach this screen at all."""
    c = _unprivileged_client(app, db)
    r = c.get("/master/vehicles/history-migration")
    assert r.status_code == 403


def test_template_downloads_with_all_three_sheets(app, db):
    import openpyxl
    from io import BytesIO
    client = _client(app, db)
    r = client.get("/master/vehicles/history-migration/template")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(BytesIO(r.data))
    assert wb.sheetnames == [
        "Instructions", "Maintenance History", "Registration History"]


def test_full_dry_run_then_real_import_then_undo_through_real_routes(
        app, db):
    """The complete cycle a person would actually go through: download
    the template, upload it as a preview, upload it for real, confirm
    real rows exist, then undo and confirm they're gone -- all through
    the actual HTTP routes, not the service layer directly."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)
    from app.modules.history_migration.models import HistoricalImportBatch

    vt = VehicleTypeService().create(code="LV-HMR", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-HMR", name="Branch HMR")
    for plate in ("SAMPLE-0001", "SAMPLE-0002"):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Test", model="Test", year=2020,
            branch_id=branch.id, conduction_number=f"CND-HMR-{plate}",
            plate_number=plate)
    db.session.commit()

    client = _client(app, db)

    template_bytes = client.get(
        "/master/vehicles/history-migration/template").data

    from io import BytesIO
    r_dry = client.post(
        "/master/vehicles/history-migration",
        data={"file": (BytesIO(template_bytes), "template.xlsx")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Preview only" in r_dry.data
    assert MaintenanceOrder.query.filter_by(is_historical=True).count() == 0

    r_real = client.post(
        "/master/vehicles/history-migration",
        data={"file": (BytesIO(template_bytes), "template.xlsx"),
             "commit": "1"},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Import complete" in r_real.data
    assert MaintenanceOrder.query.filter_by(is_historical=True).count() == 3
    assert VehicleRegistration.query.filter_by(is_historical=True).count() == 3

    batch = HistoricalImportBatch.query.order_by(
        HistoricalImportBatch.id.desc()).first()
    r_undo = client.post(
        f"/master/vehicles/history-migration/{batch.id}/delete",
        follow_redirects=True)
    assert b"Removed" in r_undo.data
    assert MaintenanceOrder.query.filter_by(is_historical=True).count() == 0
    assert VehicleRegistration.query.filter_by(is_historical=True).count() == 0


def test_recent_uploads_panel_lists_a_real_batch(app, db):
    from io import BytesIO
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService

    vt = VehicleTypeService().create(code="LV-HMR2", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-HMR2", name="Branch HMR2")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Test", model="Test", year=2020,
        branch_id=branch.id, conduction_number="CND-HMR2",
        plate_number="SAMPLE-0001")
    db.session.commit()

    client = _client(app, db)
    template_bytes = client.get(
        "/master/vehicles/history-migration/template").data
    client.post(
        "/master/vehicles/history-migration",
        data={"file": (BytesIO(template_bytes), "my_upload.xlsx"),
             "commit": "1"},
        content_type="multipart/form-data")

    html = client.get(
        "/master/vehicles/history-migration").get_data(as_text=True)
    assert "my_upload.xlsx" in html
    assert "Recent Uploads" in html


def test_link_appears_on_vehicle_list_for_a_permitted_user(app, db):
    client = _client(app, db)
    html = client.get("/master/vehicles").get_data(as_text=True)
    assert "Vehicle History Migration" in html
    assert "/master/vehicles/history-migration" in html


def test_link_is_absent_without_the_permission(app, db):
    c = _unprivileged_client(app, db, extra_permission_codes=["vehicle.view"])
    html = c.get("/master/vehicles").get_data(as_text=True)
    assert "Vehicle History Migration" not in html
