"""Tests for the REST API v1 -- the mobile app and GPS/telematics
integration surface.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


@pytest.fixture()
def api_env(db):
    sync_permissions()
    db.session.commit()
    role = Role(name="API Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "vehicle.update",
                            "maintenanceorder.view"])).all()
    db.session.add(role)
    user = User(username="apiuser", email="api@example.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add(user)

    vt = VehicleTypeService().create(code="LV-API", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-API", name="API Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="API-000",
        plate_number="API-1234")
    vehicle.current_odometer = 50000
    db.session.commit()
    return user, vehicle


def _token(client, username="apiuser", password="secret123"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": password})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── Authentication ──────────────────────────────────────────────────────────

def test_token_issued_for_valid_credentials(db, client, api_env):
    r = client.post("/api/v1/auth/token",
                    json={"username": "apiuser", "password": "secret123"})
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["access_token"]
    assert body["token_type"] == "Bearer"


def test_bad_password_rejected(db, client, api_env):
    r = client.post("/api/v1/auth/token",
                    json={"username": "apiuser", "password": "wrong"})
    assert r.status_code == 401


def test_unknown_user_gives_the_same_error_as_a_bad_password(db, client,
                                                             api_env):
    """Must not let an attacker enumerate which usernames exist."""
    r_unknown = client.post("/api/v1/auth/token",
                            json={"username": "nosuchuser",
                                 "password": "secret123"})
    r_badpw = client.post("/api/v1/auth/token",
                          json={"username": "apiuser", "password": "wrong"})
    assert r_unknown.status_code == r_badpw.status_code == 401
    assert (json.loads(r_unknown.get_data(as_text=True))["message"]
            == json.loads(r_badpw.get_data(as_text=True))["message"])


def test_request_without_token_is_rejected(db, client, api_env):
    assert client.get("/api/v1/vehicles").status_code == 401


def test_request_with_garbage_token_is_rejected(db, client, api_env):
    r = client.get("/api/v1/vehicles", headers=_auth("not-a-real-token"))
    assert r.status_code == 401


def test_deactivated_account_cannot_use_an_existing_token(db, client, api_env):
    """A token stays cryptographically valid after the account is
    disabled, so account state must be re-checked on every request."""
    user, vehicle = api_env
    token = _token(client)
    assert client.get("/api/v1/vehicles",
                      headers=_auth(token)).status_code == 200
    user.is_active = False
    db.session.commit()
    assert client.get("/api/v1/vehicles",
                      headers=_auth(token)).status_code == 401


def test_permission_is_enforced_using_the_same_codes_as_the_web_ui(
        db, client, api_env):
    """API access must never exceed what the user could do in the
    browser -- this account has no maintenanceorder.create."""
    user, vehicle = api_env
    token = _token(client)
    # Has maintenanceorder.view -> allowed
    assert client.get("/api/v1/maintenance-orders",
                      headers=_auth(token)).status_code == 200
    # Strip vehicle.update -> odometer post must become 403
    user.roles[0].permissions = [
        p for p in user.roles[0].permissions if p.code != "vehicle.update"]
    db.session.commit()
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/odometer",
                    json={"odometer": 60000}, headers=_auth(token))
    assert r.status_code == 403


# ── GPS odometer feed ───────────────────────────────────────────────────────

def test_odometer_update_records_the_reading(db, client, api_env):
    user, vehicle = api_env
    token = _token(client)
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/odometer",
                    json={"odometer": 55000}, headers=_auth(token))
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["previous_odometer"] == 50000
    assert body["current_odometer"] == 55000
    assert vehicle.current_odometer == 55000


def test_odometer_cannot_go_backwards(db, client, api_env):
    """A GPS glitch, device swap or replayed message must not rewind the
    odometer -- that would silently reset PM scheduling."""
    user, vehicle = api_env
    token = _token(client)
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/odometer",
                    json={"odometer": 100}, headers=_auth(token))
    assert r.status_code == 409
    assert vehicle.current_odometer == 50000  # unchanged


def test_odometer_response_includes_pm_status(db, client, api_env):
    """The telematics platform learns immediately whether the reading it
    just sent has made a service due, without a second call."""
    user, vehicle = api_env
    token = _token(client)
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/odometer",
                    json={"odometer": 55000}, headers=_auth(token))
    body = json.loads(r.get_data(as_text=True))
    assert "pm_status" in body
    assert "status" in body["pm_status"]


def test_non_numeric_odometer_is_rejected(db, client, api_env):
    user, vehicle = api_env
    token = _token(client)
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/odometer",
                    json={"odometer": "abc"}, headers=_auth(token))
    assert r.status_code == 400


def test_missing_odometer_is_rejected(db, client, api_env):
    user, vehicle = api_env
    token = _token(client)
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/odometer",
                    json={}, headers=_auth(token))
    assert r.status_code == 400


def test_comma_formatted_odometer_is_accepted(db, client, api_env):
    """Devices and integrations send '55,000' surprisingly often."""
    user, vehicle = api_env
    token = _token(client)
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/odometer",
                    json={"odometer": "55,000"}, headers=_auth(token))
    assert r.status_code == 200
    assert vehicle.current_odometer == 55000


# ── Read endpoints ──────────────────────────────────────────────────────────

def test_vehicle_list_and_plate_filter(db, client, api_env):
    user, vehicle = api_env
    token = _token(client)
    r = client.get("/api/v1/vehicles?plate=API-1234", headers=_auth(token))
    body = json.loads(r.get_data(as_text=True))
    assert body["count"] == 1
    assert body["results"][0]["plate_number"] == "API-1234"


def test_me_reports_roles_and_permissions(db, client, api_env):
    token = _token(client)
    body = json.loads(client.get("/api/v1/me",
                                 headers=_auth(token)).get_data(as_text=True))
    assert body["username"] == "apiuser"
    assert "vehicle.view" in body["permissions"]


def test_unknown_vehicle_returns_404(db, client, api_env):
    token = _token(client)
    r = client.get("/api/v1/vehicles/999999", headers=_auth(token))
    assert r.status_code == 404
