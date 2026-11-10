"""The ATD API (list and detail -- both share `_atd_json`) carries a
`display_status` field, the same shared resolver the desktop MO list
uses, so an approved-but-not-yet-activated ATD reads correctly instead
of just "DRAFT" (2026-11-10)."""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.approval_config.service import (
    ApprovalMatrixService, ApprovalPathService)
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import DocumentTypeService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.atd.service import ATDService
from app.modules.user_management.models import Permission, Role, User
from tests.conftest import tiny_jpeg_file


@pytest.fixture()
def env(db):
    sync_permissions()
    dt = DocumentType.query.filter_by(code="ATD").first()
    if dt is None:
        dt = DocumentTypeService().create(code="ATD", name="ATD",
                                          requires_approval=True,
                                          auto_numbering=False)
    branch = BranchService().create(code="BR-DS", name="Status Hub")
    vt = VehicleTypeService().create(code="LV-DS", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="DS-001")
    driver = DriverService().create(
        employee_number="EMP-DS1", first_name="Maria", last_name="Santos",
        license_number="LIC-DS1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id,
        photo_file=tiny_jpeg_file())
    approver_role = Role(name="DS approvers")
    viewer_role = Role(name="DS viewers", permissions=Permission.query.filter(
        Permission.code.in_(["atd.view"])).all())
    db.session.add_all([approver_role, viewer_role])
    db.session.commit()
    user = User(username="ds_viewer", email="ds@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [viewer_role, approver_role]
    db.session.add(user)
    db.session.commit()

    path = ApprovalPathService().create(name="DS Path", levels=[
        {"level_number": 1, "approver_type": "ROLE",
         "role_id": approver_role.id}])
    ApprovalMatrixService().create(dt.id, path.id)
    return dict(vehicle=vehicle, driver=driver, user=user)


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "ds_viewer", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_an_approved_but_not_yet_activated_atd_shows_as_approved(
        db, client, env):
    """Submitted and approved, but `activate()` hasn't run yet -- the
    exact gap: status is still "DRAFT", so without the shared resolver
    this showed as "Draft", indistinguishable from one never submitted."""
    svc = ATDService()
    atd = svc.create(
        vehicle_id=env["vehicle"].id, driver_id=env["driver"].id,
        purpose="Client visit", valid_from=date(2026, 7, 15),
        valid_to=date(2026, 7, 31), user=env["user"])
    svc.submit(atd.id, user=env["user"])
    svc.approve(atd.id, user=env["user"])

    r = client.get(f"/api/v1/atd/{atd.id}", headers=_hdr(client))
    body = json.loads(r.get_data(as_text=True))
    assert body["status"] == "DRAFT"
    assert body["display_status"] == "Approved"


def test_an_unsubmitted_draft_still_shows_as_draft(db, client, env):
    svc = ATDService()
    atd = svc.create(
        vehicle_id=env["vehicle"].id, driver_id=env["driver"].id,
        purpose="Client visit", valid_from=date(2026, 7, 15),
        valid_to=date(2026, 7, 31), user=env["user"])
    r = client.get(f"/api/v1/atd/{atd.id}", headers=_hdr(client))
    body = json.loads(r.get_data(as_text=True))
    assert body["display_status"] == "Draft"
