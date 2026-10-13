"""An expired Authority To Drive is finished -- not editable (2026-10-13).

Michael: "for the ATD once the request already expired this cannot be
editable, the initiator need a new request for new date coverage of ATD."

The edit policy opens a document while its work is ongoing, and an
ACTIVE ATD is ongoing -- but only until its Valid To date passes. After
that the authority has lapsed: extending the dates on the old slip would
re-authorise a trip that was never approved for those days, and would
leave the gate guard's copy disagreeing with the record. So:

* an ATD past Valid To is closed for editing, whatever its stored
  status (nothing flips ACTIVE -> EXPIRED in real time, KI-10);
* `expire_overdue()` does that flip, for the daily scan and the CLI, so
  the list and the pill say EXPIRED rather than "APPROVED & ACTIVE";
* the refusal tells the person to raise a new ATD, because that is the
  only way to get authority for new dates.
"""
import json
from datetime import date, timedelta

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import DocumentTypeService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.atd.models import AuthorityToDrive
from app.modules.transactions.atd.service import ATDService
from app.modules.transactions.base_service import DocumentStateError
from app.modules.user_management.models import Permission, Role, User
from tests.conftest import tiny_jpeg_file

TODAY = date.today()


@pytest.fixture()
def env(db):
    sync_permissions()
    if DocumentType.query.filter_by(code="ATD").first() is None:
        DocumentTypeService().create(code="ATD", name="ATD",
                                     requires_approval=True,
                                     auto_numbering=False)
    branch = BranchService().create(code="BR-EX", name="Manila Hub")
    vt = VehicleTypeService().create(code="LV-EX", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2012,
        branch_id=branch.id, conduction_number="EXP-120")
    driver = DriverService().create(
        employee_number="EMP-EX1", first_name="Maria", last_name="Santos",
        license_number="LIC-EX1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id,
        photo_file=tiny_jpeg_file())
    role = Role(name="EX editors", permissions=Permission.query.filter(
        Permission.code.in_(["atd.view", "atd.update"])).all())
    db.session.add(role)
    db.session.commit()
    user = User(username="ex_editor", email="ex@e.com", first_name="Ex",
                last_name="Editor", password_hash=hash_password("secret123"),
                is_active=True)
    user.roles = [role]
    db.session.add(user)
    db.session.commit()
    return dict(vehicle=vehicle, driver=driver, user=user)


def _atd(env, *, valid_from, valid_to, status="ACTIVE"):
    from app.extensions import db
    atd = ATDService().create(
        vehicle_id=env["vehicle"].id, driver_id=env["driver"].id,
        purpose="Field visit", valid_from=valid_from, valid_to=valid_to,
        user=env["user"])
    atd.status = status
    db.session.commit()
    return atd


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "ex_editor", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_active_atd_within_its_dates_is_still_editable(db, env):
    atd = _atd(env, valid_from=TODAY - timedelta(days=1),
               valid_to=TODAY + timedelta(days=2))
    svc = ATDService()
    assert svc.editable_scope(atd) == "FULL"
    assert svc.can_edit(atd, env["user"])


def test_an_atd_valid_only_through_today_is_still_editable(db, env):
    """Valid To is inclusive -- the authority runs to the end of that
    day, so the last day of a trip is not too late to fix an odometer."""
    atd = _atd(env, valid_from=TODAY, valid_to=TODAY)
    assert ATDService().editable_scope(atd) == "FULL"


def test_an_atd_past_its_valid_to_is_closed(db, env):
    atd = _atd(env, valid_from=TODAY - timedelta(days=10),
               valid_to=TODAY - timedelta(days=1))
    svc = ATDService()
    assert svc.editable_scope(atd) == "NONE"
    assert not svc.can_edit(atd, env["user"])


def test_editing_an_expired_atd_says_to_raise_a_new_one(db, env):
    atd = _atd(env, valid_from=TODAY - timedelta(days=10),
               valid_to=TODAY - timedelta(days=1))
    with pytest.raises(DocumentStateError) as exc:
        ATDService().update(atd.id, user=env["user"],
                            valid_to=TODAY + timedelta(days=5))
    assert "new" in str(exc.value).lower()


def test_a_stored_expired_status_is_closed_too(db, env):
    """Once the daily scan has flipped it, the date no longer matters."""
    atd = _atd(env, valid_from=TODAY, valid_to=TODAY, status="EXPIRED")
    assert ATDService().editable_scope(atd) == "NONE"


def test_a_draft_atd_with_past_dates_can_still_be_corrected(db, env):
    """A draft was never authority for anything -- the whole point of
    editing it is to fix dates that are now in the past."""
    atd = _atd(env, valid_from=TODAY - timedelta(days=5),
               valid_to=TODAY - timedelta(days=2), status="DRAFT")
    assert ATDService().editable_scope(atd) == "FULL"


def test_expire_overdue_flips_lapsed_active_permits(db, env):
    lapsed = _atd(env, valid_from=TODAY - timedelta(days=10),
                  valid_to=TODAY - timedelta(days=1))
    current = _atd(env, valid_from=TODAY, valid_to=TODAY + timedelta(days=3))
    draft = _atd(env, valid_from=TODAY - timedelta(days=9),
                 valid_to=TODAY - timedelta(days=5), status="DRAFT")
    assert ATDService().expire_overdue() == 1
    assert db.session.get(AuthorityToDrive, lapsed.id).status == "EXPIRED"
    assert db.session.get(AuthorityToDrive, current.id).status == "ACTIVE"
    assert db.session.get(AuthorityToDrive, draft.id).status == "DRAFT"


def test_expire_overdue_is_idempotent(db, env):
    _atd(env, valid_from=TODAY - timedelta(days=10),
         valid_to=TODAY - timedelta(days=1))
    svc = ATDService()
    assert svc.expire_overdue() == 1
    assert svc.expire_overdue() == 0


def test_api_detail_reports_a_lapsed_permit_as_expired(client, db, env):
    atd = _atd(env, valid_from=TODAY - timedelta(days=10),
               valid_to=TODAY - timedelta(days=1))
    body = client.get(f"/api/v1/atd/{atd.id}", headers=_hdr(client)).get_json()
    assert body["is_expired"] is True
    assert body["can_edit"] is False


def test_api_patch_on_an_expired_permit_is_a_conflict(client, db, env):
    atd = _atd(env, valid_from=TODAY - timedelta(days=10),
               valid_to=TODAY - timedelta(days=1))
    r = client.patch(f"/api/v1/atd/{atd.id}",
                     json={"purpose": "Another trip"}, headers=_hdr(client))
    assert r.status_code == 409, r.get_data(as_text=True)


def test_api_detail_of_a_live_permit_is_not_expired(client, db, env):
    atd = _atd(env, valid_from=TODAY, valid_to=TODAY + timedelta(days=2))
    body = client.get(f"/api/v1/atd/{atd.id}", headers=_hdr(client)).get_json()
    assert body["is_expired"] is False
    assert body["can_edit"] is True
