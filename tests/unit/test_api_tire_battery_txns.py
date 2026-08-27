"""Tire and Battery Transaction APIs.

Phase 1 scope in the project's master prompt, with zero API coverage
before this. Both modules follow the same shape and are tested
together so the two cannot drift apart.

What makes these different from every other transaction module: they
have a PHYSICAL EFFECT. Completing one changes the tire's or battery's
own status in Master Data (MOUNTED / IN_STOCK / RETREADED / DISPOSED),
and DISPOSE also deactivates the record. The service defers that
effect until approval when the Document Type requires approval, and
applies it immediately when it does not. Both timings are tested,
because an asset silently changing state at the wrong moment is the
kind of fault that is invisible until someone counts stock.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code, *, requires_approval=False):
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code=code).first()
    if dt is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=requires_approval,
                                     auto_numbering=True,
                                     attachment_allowed=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    dt.requires_approval = requires_approval
    return dt


@pytest.fixture()
def tb_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Asset Clerk")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "tire.view", "tire.create", "tire.update",
        "battery.view", "battery.create", "battery.update"])).all()
    user = User(username="assetclerk", email="ac@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="Asset Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code.in_(["tire.view", "battery.view"])).all()
    viewer = User(username="assetviewer", email="av2@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    db.session.add_all([role, user, viewer_role, viewer])

    branch = BranchService().create(code="BR-TB", name="TB Branch")
    vt = VehicleTypeService().create(code="LV-TB", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2024,
        branch_id=branch.id, conduction_number="TB-000")

    from app.modules.master_data.tire.models import Tire
    from app.modules.master_data.battery.models import Battery

    tire = Tire(serial_number="TIRE-001", brand="Bridgestone",
                size="265/65R17", tire_type="RADIAL",
                status="IN_STOCK", branch_id=branch.id, is_active=True)
    battery = Battery(serial_number="BATT-001", brand="Motolite",
                      status="IN_STOCK", branch_id=branch.id, is_active=True)
    db.session.add_all([tire, battery])
    _ensure_doc_type("TIR")
    _ensure_doc_type("BAT")
    db.session.commit()
    return {"user": user, "branch": branch, "vehicle": vehicle,
            "tire": tire, "battery": battery}


def _token(client, username="assetclerk"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _post(client, path, token, payload=None):
    r = client.post(path, json=payload or {},
                    headers={"Authorization": f"Bearer {token}"})
    body = r.get_data(as_text=True)
    return r.status_code, (json.loads(body) if body else {})


# ── Auth ────────────────────────────────────────────────────────────────────

def test_tire_list_rejects_anonymous(db, client, tb_env):
    assert client.get("/api/v1/tire-transactions").status_code == 401


def test_battery_list_rejects_anonymous(db, client, tb_env):
    assert client.get("/api/v1/battery-transactions").status_code == 401


def test_tire_create_requires_the_create_permission(db, client, tb_env):
    status, _ = _post(client, "/api/v1/tire-transactions",
                      _token(client, "assetviewer"),
                      {"tire_id": tb_env["tire"].id, "action": "MOUNT",
                       "transaction_date": date.today().isoformat()})
    assert status == 403


# ── Action validation ───────────────────────────────────────────────────────

def test_an_unknown_tire_action_is_refused_and_names_the_valid_ones(
        db, client, tb_env):
    status, body = _post(client, "/api/v1/tire-transactions", _token(client),
                         {"tire_id": tb_env["tire"].id, "action": "LAUNCH",
                          "transaction_date": date.today().isoformat()})
    assert status == 400, body
    assert "MOUNT" in json.dumps(body)


def test_batteries_do_not_accept_RETREAD(db, client, tb_env):
    """Tires can be retreaded; batteries cannot. The two modules look
    alike, and this is exactly the difference that would be lost if one
    were copied from the other."""
    status, body = _post(client, "/api/v1/battery-transactions",
                         _token(client),
                         {"battery_id": tb_env["battery"].id,
                          "action": "RETREAD",
                          "transaction_date": date.today().isoformat()})
    assert status == 400, body


def test_tires_do_accept_RETREAD(db, client, tb_env):
    status, body = _post(client, "/api/v1/tire-transactions", _token(client),
                         {"tire_id": tb_env["tire"].id, "action": "RETREAD",
                          "transaction_date": date.today().isoformat()})
    assert status == 201, body


# ── The physical effect: WHEN the asset's status actually changes ──────────

def test_mounting_a_tire_changes_its_status_immediately_when_no_approval_needed(
        db, client, tb_env):
    """With the Document Type not requiring approval, the service
    applies the physical effect on create -- the tire is genuinely
    mounted, not merely recorded as intended."""
    from app.modules.master_data.tire.models import Tire

    _ensure_doc_type("TIR", requires_approval=False)
    db.session.commit()

    status, body = _post(client, "/api/v1/tire-transactions", _token(client),
                         {"tire_id": tb_env["tire"].id, "action": "MOUNT",
                          "vehicle_id": tb_env["vehicle"].id,
                          "transaction_date": date.today().isoformat()})
    assert status == 201, body
    assert body["status"] == "COMPLETED"

    fresh = Tire.query.filter_by(id=tb_env["tire"].id).first()
    assert fresh.status == "MOUNTED"


def test_the_tire_status_does_NOT_change_before_approval_when_one_is_required(
        db, client, tb_env):
    """The point of requiring approval: the asset must not move until
    someone approves it. A tire that changes state on submission would
    make the approval meaningless and the stock count wrong."""
    from app.modules.master_data.tire.models import Tire

    _ensure_doc_type("TIR", requires_approval=True)
    db.session.commit()

    status, body = _post(client, "/api/v1/tire-transactions", _token(client),
                         {"tire_id": tb_env["tire"].id, "action": "MOUNT",
                          "transaction_date": date.today().isoformat()})
    assert status == 201, body
    assert body["status"] == "DRAFT"

    fresh = Tire.query.filter_by(id=tb_env["tire"].id).first()
    assert fresh.status == "IN_STOCK"


def test_disposing_a_battery_deactivates_the_record(db, client, tb_env):
    """DISPOSE is the one action that also deactivates: a disposed
    battery must stop appearing in pickers as if it were available
    stock."""
    from app.modules.master_data.battery.models import Battery

    _ensure_doc_type("BAT", requires_approval=False)
    db.session.commit()

    status, body = _post(client, "/api/v1/battery-transactions",
                         _token(client),
                         {"battery_id": tb_env["battery"].id,
                          "action": "DISPOSE",
                          "transaction_date": date.today().isoformat()})
    assert status == 201, body

    fresh = Battery.query.filter_by(id=tb_env["battery"].id).first()
    assert fresh.status == "DISPOSED"
    assert fresh.is_active is False


def test_dismounting_returns_a_tire_to_stock(db, client, tb_env):
    from app.modules.master_data.tire.models import Tire

    _ensure_doc_type("TIR", requires_approval=False)
    tb_env["tire"].status = "MOUNTED"
    db.session.commit()

    _post(client, "/api/v1/tire-transactions", _token(client),
          {"tire_id": tb_env["tire"].id, "action": "DISMOUNT",
           "transaction_date": date.today().isoformat()})

    fresh = Tire.query.filter_by(id=tb_env["tire"].id).first()
    assert fresh.status == "IN_STOCK"


# ── Detail / list ───────────────────────────────────────────────────────────

def test_tire_detail_404s_for_a_missing_transaction(db, client, tb_env):
    status, _ = _get(client, "/api/v1/tire-transactions/999999",
                     _token(client))
    assert status == 404


def test_tire_list_shows_created_transactions(db, client, tb_env):
    _post(client, "/api/v1/tire-transactions", _token(client),
          {"tire_id": tb_env["tire"].id, "action": "MOUNT",
           "transaction_date": date.today().isoformat()})
    status, body = _get(client, "/api/v1/tire-transactions", _token(client))
    assert status == 200
    assert body["items"][0]["action"] == "MOUNT"
    # The serial is what identifies a tire to a storeman -- an id alone
    # would send them back to Master Data to look it up.
    assert body["items"][0]["tire_serial"] == "TIRE-001"


def test_battery_list_shows_created_transactions(db, client, tb_env):
    _post(client, "/api/v1/battery-transactions", _token(client),
          {"battery_id": tb_env["battery"].id, "action": "MOUNT",
           "transaction_date": date.today().isoformat()})
    status, body = _get(client, "/api/v1/battery-transactions",
                        _token(client))
    assert status == 200
    assert body["items"][0]["battery_serial"] == "BATT-001"


def test_form_options_report_the_valid_actions(db, client, tb_env):
    """The form must offer exactly what the service accepts -- and the
    two modules' action sets genuinely differ."""
    _status, tire_opts = _get(client, "/api/v1/tire-transactions/form-options",
                              _token(client))
    _status, batt_opts = _get(
        client, "/api/v1/battery-transactions/form-options", _token(client))
    assert "RETREAD" in tire_opts["actions"]
    assert "RETREAD" not in batt_opts["actions"]


# ── Lifecycle ───────────────────────────────────────────────────────────────

def test_lifecycle_actions_are_routed(db, client, tb_env):
    _ensure_doc_type("TIR", requires_approval=True)
    db.session.commit()
    _status, txn = _post(client, "/api/v1/tire-transactions", _token(client),
                         {"tire_id": tb_env["tire"].id, "action": "MOUNT",
                          "transaction_date": date.today().isoformat()})
    for action in ("submit", "approve", "reject", "cancel"):
        status, _ = _post(
            client, f"/api/v1/tire-transactions/{txn['id']}/{action}",
            _token(client))
        assert status != 404, f"/{action} is not routed"
