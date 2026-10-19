"""Mobile: "Before you drive" -- the readiness checklist on My Vehicle.

Michael chose direction C of the My Vehicle mockups (2026-10-14): the
Overview tab opens with one card that answers "can I drive this today?"
as a short checklist -- Registration, Insurance, PMS, open work, and the
driver's active Authority To Drive when they hold one -- and a headline
of "Ready to drive" or "N things to check".

GET /my/vehicles/<id>/readiness. Every check is computed by the SAME
service the desktop uses for that fact (RegistrationDueCalculationService,
PMDueCalculationService, the vehicle's own insurance cover dates, the
maintenance orders, ATDService.is_expired), so the phone and the Fleet
Office screens can never disagree about whether a vehicle is due.

States: ok | warn | alert | unknown. "unknown" is an honest answer --
no PMS schedule, no insurance dates on file -- and never counts as a
thing to check: the driver cannot fix missing master data.
"""
import json
from datetime import date, timedelta

import pytest

from app.core.approval.models import ApprovalInstance
from app.core.security.password import hash_password
from app.extensions import db
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import DocumentTypeService
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.assignment_service import (
    VehicleAssignmentService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.atd.models import AuthorityToDrive
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.user_management.models import Permission, Role, User

TODAY = date.today()


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p); db.session.flush()
    return p


def _user(username, branch_id):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role); db.session.flush()
    role.permissions.append(_perm("vehicle.view"))
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=branch_id, mobile_access=True)
    u.roles.append(role)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture()
def env(app):
    b = Branch(code="RDY", name="Muntinlupa")
    vt = VehicleType(code="LT-RDY", name="Light", category="LIGHT")
    db.session.add_all([b, vt]); db.session.commit()
    juan_u = _user("rdy_juan", b.id)
    maria_u = _user("rdy_maria", b.id)
    juan = Driver(person_id="PID-RDY-1", employee_number="EMP-RDY-1",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-RDY-2", employee_number="EMP-RDY-2",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria]); db.session.commit()
    DriverService().link_user(juan.id, juan_u.id)
    DriverService().link_user(maria.id, maria_u.id)
    mine = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2009,
        branch_id=b.id, conduction_number="RDY-A", plate_number="NZO-629")
    theirs = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="DMax", year=2019,
        branch_id=b.id, conduction_number="RDY-B", plate_number="NAB-4471")
    db.session.commit()
    VehicleAssignmentService().assign(mine.id, juan.id, source="MANUAL")
    VehicleAssignmentService().assign(theirs.id, maria.id, source="MANUAL")
    return dict(mine=mine, theirs=theirs, juan=juan_u, juan_driver=juan,
                maria_driver=maria)


def _hdr(client, username="rdy_juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization":
            f"Bearer {json.loads(r.get_data(as_text=True))['access_token']}"}


def _get(client, env, vid=None):
    r = client.get(f"/api/v1/my/vehicles/{vid or env['mine'].id}/readiness",
                   headers=_hdr(client))
    return r.status_code, json.loads(r.get_data(as_text=True))


def _check(body, key):
    found = [c for c in body["checks"] if c["key"] == key]
    return found[0] if found else None


@pytest.fixture()
def pms(monkeypatch):
    """Control the PMS answer without building a whole PM schedule --
    the mapping from the service's per-type items to the checklist is
    what is under test, and the service has its own suite
    (test_pm_vehicle_due_items)."""
    holder = {"items": []}
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    monkeypatch.setattr(PMDueCalculationService, "vehicle_due_items",
                        lambda self, vehicle, *a, **k: holder["items"])
    return holder


def _item(name, status, due_km=None, due_date=None, type_id=1,
          open_order=None):
    return {"maintenance_type_id": type_id, "maintenance_type": name,
            "code": None, "equipment_kind": None, "status": status,
            "next_due_km": due_km, "next_due_date": due_date,
            "interval_km": None, "interval_days": None, "last_km": None,
            "last_date": None, "open_order": open_order}


# ── scope ────────────────────────────────────────────────────────────

def test_404_for_a_vehicle_the_caller_does_not_hold(client, env, pms):
    status, _ = _get(client, env, env["theirs"].id)
    assert status == 404


# ── registration ─────────────────────────────────────────────────────

def test_registration_valid_is_ok(client, env, pms):
    env["mine"].last_known_registration_expiry = TODAY + timedelta(days=200)
    db.session.commit()
    _, body = _get(client, env)
    reg = _check(body, "registration")
    assert reg["state"] == "ok"
    assert reg["date"] == (TODAY + timedelta(days=200)).isoformat()


def test_registration_due_soon_is_a_warning_with_days_left(client, env, pms):
    env["mine"].last_known_registration_expiry = TODAY + timedelta(days=18)
    db.session.commit()
    _, body = _get(client, env)
    reg = _check(body, "registration")
    assert reg["state"] == "warn"
    assert reg["days_left"] == 18
    assert "18 days" in reg["detail"]


def test_registration_expired_is_an_alert(client, env, pms):
    env["mine"].last_known_registration_expiry = TODAY - timedelta(days=3)
    db.session.commit()
    _, body = _get(client, env)
    reg = _check(body, "registration")
    assert reg["state"] == "alert"
    assert "Expired" in reg["detail"]


# ── insurance ────────────────────────────────────────────────────────

def test_insurance_without_dates_is_unknown_not_a_thing_to_check(
        client, env, pms):
    _, body = _get(client, env)
    ins = _check(body, "insurance")
    assert ins["state"] == "unknown"


def test_insurance_follows_the_earliest_cover_to_expire(client, env, pms):
    env["mine"].ctpl_to_date = TODAY + timedelta(days=10)
    env["mine"].od_theft_aon_to_date = TODAY + timedelta(days=300)
    db.session.commit()
    _, body = _get(client, env)
    ins = _check(body, "insurance")
    assert ins["state"] == "warn"
    assert "CTPL" in ins["detail"]


def test_insurance_lapsed_is_an_alert(client, env, pms):
    env["mine"].ctpl_to_date = TODAY - timedelta(days=1)
    db.session.commit()
    assert _check(_get(client, env)[1], "insurance")["state"] == "alert"


def test_insurance_valid_is_ok(client, env, pms):
    env["mine"].ctpl_to_date = TODAY + timedelta(days=150)
    db.session.commit()
    assert _check(_get(client, env)[1], "insurance")["state"] == "ok"


# ── PMS ──────────────────────────────────────────────────────────────

def test_no_pms_schedule_is_unknown(client, env, pms):
    assert _check(_get(client, env)[1], "pms")["state"] == "unknown"


def test_pms_due_soon_names_the_km_to_go(client, env, pms):
    env["mine"].current_odometer = 38750
    db.session.commit()
    pms["items"] = [_item("Preventive Maintenance Service", "DUE_SOON",
                          due_km=40000)]
    item = _check(_get(client, env)[1], "pms")
    assert item["state"] == "warn"
    assert "40,000 km" in item["detail"]
    assert "1,250 km to go" in item["detail"]


def test_pms_overdue_is_an_alert(client, env, pms):
    env["mine"].current_odometer = 41000
    db.session.commit()
    pms["items"] = [_item("Preventive Maintenance Service", "OVERDUE",
                          due_km=40000)]
    item = _check(_get(client, env)[1], "pms")
    assert item["state"] == "alert"
    assert "1,000 km over" in item["detail"]


def test_pms_good_is_ok(client, env, pms):
    env["mine"].current_odometer = 36000
    db.session.commit()
    pms["items"] = [_item("Preventive Maintenance Service", "GOOD",
                          due_km=40000)]
    assert _check(_get(client, env)[1], "pms")["state"] == "ok"


def test_pms_row_names_which_maintenance_is_due(client, env, pms):
    """Reported 2026-10-19: "PMS -- Due at 45,000 km" did not say whether
    it was aircon servicing, preventive maintenance or something else."""
    env["mine"].current_odometer = 45100
    db.session.commit()
    pms["items"] = [_item("Aircon Servicing", "OVERDUE", due_km=45000)]
    item = _check(_get(client, env)[1], "pms")
    assert item["label"] == "Aircon Servicing"


def test_a_second_due_type_gets_its_own_row(client, env, pms):
    env["mine"].current_odometer = 45100
    db.session.commit()
    pms["items"] = [
        _item("Preventive Maintenance Service", "OVERDUE", due_km=45000,
              type_id=1),
        _item("Aircon Servicing", "DUE_SOON", due_km=45300, type_id=4),
        _item("Tire Rotation", "GOOD", due_km=50000, type_id=2)]
    _, body = _get(client, env)
    assert _check(body, "pms")["label"] == "Preventive Maintenance Service"
    assert _check(body, "pms-4")["label"] == "Aircon Servicing"
    assert _check(body, "pms-4")["state"] == "warn"
    # Not due: no row of its own -- the card is "before you drive".
    assert _check(body, "pms-2") is None


def test_pms_row_mentions_an_order_already_raised(client, env, pms):
    env["mine"].current_odometer = 45100
    db.session.commit()
    pms["items"] = [_item("Preventive Maintenance Service", "OVERDUE",
                          due_km=45000, open_order={
                              "id": 9, "document_number": "MO-2026-000033",
                              "status": "DRAFT"})]
    assert "MO-2026-000033" in _check(_get(client, env)[1], "pms")["detail"]


# ── open work ────────────────────────────────────────────────────────

def _mo(env, status, *, pending=False, description="Brakes"):
    mo = MaintenanceOrder(document_number=f"MO-RDY-{status}",
                          vehicle_id=env["mine"].id,
                          order_category="MAINTENANCE",
                          scheduled_date=TODAY, description=description,
                          status=status)
    db.session.add(mo); db.session.flush()
    if pending:
        if DocumentType.query.filter_by(code="MO").first() is None:
            DocumentTypeService().create(code="MO", name="MO",
                                         requires_approval=True,
                                         auto_numbering=False)
        dt = DocumentType.query.filter_by(code="MO").first()
        inst = ApprovalInstance(document_type_id=dt.id, approval_path_id=None,
                                reference_table="maintenance_orders",
                                reference_id=mo.id, status="PENDING",
                                current_level=1, submitted_by=env["juan"].id)
        db.session.add(inst); db.session.flush()
        mo.approval_instance_id = inst.id
    db.session.commit()
    return mo


def test_nothing_in_the_shop_is_ok(client, env, pms):
    item = _check(_get(client, env)[1], "work")
    assert item["state"] == "ok"


def test_an_order_in_the_workshop_is_an_alert(client, env, pms):
    """The unit is at the shop -- the one fact that most changes whether
    the driver can use it today."""
    _mo(env, "IN_PROGRESS", description="Corrective — Brake System")
    item = _check(_get(client, env)[1], "work")
    assert item["state"] == "alert"
    assert "workshop" in item["detail"].lower()


def test_an_order_awaiting_approval_is_a_warning(client, env, pms):
    _mo(env, "DRAFT", pending=True)
    assert _check(_get(client, env)[1], "work")["state"] == "warn"


def test_completed_orders_are_not_open_work(client, env, pms):
    _mo(env, "COMPLETED")
    assert _check(_get(client, env)[1], "work")["state"] == "ok"


# ── ATD ──────────────────────────────────────────────────────────────

def test_an_active_atd_for_this_driver_appears_as_ok(client, env, pms):
    db.session.add(AuthorityToDrive(
        document_number="ATD-RDY-1", vehicle_id=env["mine"].id,
        driver_id=env["juan_driver"].id, purpose="Field visit",
        valid_from=TODAY, valid_to=TODAY + timedelta(days=5),
        status="ACTIVE"))
    db.session.commit()
    item = _check(_get(client, env)[1], "atd")
    assert item["state"] == "ok"
    assert "ATD-RDY-1" in item["detail"]


def test_no_atd_row_when_the_driver_holds_none(client, env, pms):
    """An ATD is not required to drive your own assigned unit, so a
    missing one is not a thing to check -- the row simply isn't there."""
    assert _check(_get(client, env)[1], "atd") is None


def test_an_expired_atd_is_not_shown_as_active(client, env, pms):
    db.session.add(AuthorityToDrive(
        document_number="ATD-RDY-OLD", vehicle_id=env["mine"].id,
        driver_id=env["juan_driver"].id, purpose="Old",
        valid_from=TODAY - timedelta(days=9),
        valid_to=TODAY - timedelta(days=2), status="ACTIVE"))
    db.session.commit()
    assert _check(_get(client, env)[1], "atd") is None


def test_someone_elses_atd_on_my_vehicle_is_not_mine(client, env, pms):
    db.session.add(AuthorityToDrive(
        document_number="ATD-RDY-HERS", vehicle_id=env["mine"].id,
        driver_id=env["maria_driver"].id, purpose="Pool trip",
        valid_from=TODAY, valid_to=TODAY + timedelta(days=2),
        status="ACTIVE"))
    db.session.commit()
    assert _check(_get(client, env)[1], "atd") is None


# ── headline and assignment ──────────────────────────────────────────

def test_attention_count_counts_warnings_and_alerts_only(client, env, pms):
    env["mine"].last_known_registration_expiry = TODAY + timedelta(days=18)
    db.session.commit()
    _mo(env, "IN_PROGRESS")
    _, body = _get(client, env)
    # registration warn + work alert; insurance/pms unknown don't count.
    assert body["attention_count"] == 2


def test_ready_when_nothing_needs_attention(client, env, pms):
    env["mine"].last_known_registration_expiry = TODAY + timedelta(days=200)
    db.session.commit()
    assert _get(client, env)[1]["attention_count"] == 0


def test_assignment_says_since_when_and_which_branch(client, env, pms):
    _, body = _get(client, env)
    assert body["assignment"]["branch"] == "Muntinlupa"
    assert body["assignment"]["since"] is not None
