"""Odometer update by plate number -- the API entry point for a source
system (SMS-consolidation, a fleet-card reader) that only knows a
vehicle's plate, not this system's internal id.
"""
import pytest


@pytest.fixture()
def token_headers(app, db):
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    r = c.post("/api/v1/auth/token",
              json={"username": "admin", "password": "Testpass123!"})
    token = r.get_json()["access_token"]
    return c, {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def vehicle(db):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-APIOD", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-APIOD", name="Branch APIOD")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="APIOD-1",
        plate_number="APIOD-999")
    v.current_odometer = 40000
    db.session.commit()
    return v


def test_update_by_plate_succeeds(token_headers, vehicle):
    client, headers = token_headers
    r = client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
                    json={"plate_number": "APIOD-999", "odometer": 45320})
    assert r.status_code == 200
    body = r.get_json()
    assert body["current_odometer"] == 45320
    assert body["previous_odometer"] == 40000
    assert body["vehicle_id"] == vehicle.id


def test_plate_lookup_is_case_insensitive(token_headers, vehicle):
    client, headers = token_headers
    r = client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
                    json={"plate_number": "apiod-999", "odometer": 41000})
    assert r.status_code == 200


def test_backwards_reading_is_rejected(token_headers, vehicle):
    client, headers = token_headers
    client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
               json={"plate_number": "APIOD-999", "odometer": 45320})
    r = client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
                    json={"plate_number": "APIOD-999", "odometer": 44000})
    assert r.status_code == 409
    assert r.get_json()["current_odometer"] == 45320


def test_unknown_plate_returns_404(token_headers, vehicle):
    client, headers = token_headers
    r = client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
                    json={"plate_number": "NO-SUCH-PLATE", "odometer": 1000})
    assert r.status_code == 404


def test_missing_plate_number_is_a_400_not_a_500(token_headers):
    client, headers = token_headers
    r = client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
                    json={"odometer": 1000})
    assert r.status_code == 400


def test_missing_odometer_is_a_400(token_headers, vehicle):
    client, headers = token_headers
    r = client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
                    json={"plate_number": "APIOD-999"})
    assert r.status_code == 400


def test_non_numeric_odometer_is_a_400_not_a_500(token_headers, vehicle):
    client, headers = token_headers
    r = client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
                    json={"plate_number": "APIOD-999", "odometer": "abc"})
    assert r.status_code == 400


def test_request_without_a_token_is_401(app, db, vehicle):
    client = app.test_client()
    r = client.post("/api/v1/vehicles/by-plate/odometer",
                    json={"plate_number": "APIOD-999", "odometer": 1000})
    assert r.status_code == 401


def test_conduction_number_also_matches(token_headers, db):
    """A vehicle with no plate yet (common for a brand-new unit) must
    still be reachable via its conduction number."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-APIOD2", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-APIOD2", name="Branch APIOD2")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="COND-APIOD-2")
    client, headers = token_headers
    r = client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
                    json={"plate_number": "COND-APIOD-2", "odometer": 500})
    assert r.status_code == 200


def test_the_vehicle_id_endpoint_and_the_by_plate_endpoint_enforce_the_same_rule(
        token_headers, vehicle):
    """Both entry points share one validation function -- confirms they
    can't drift apart and enforce different rules for the same kind of
    update."""
    client, headers = token_headers
    client.post("/api/v1/vehicles/by-plate/odometer", headers=headers,
               json={"plate_number": "APIOD-999", "odometer": 50000})
    # Now try to go backwards through the OTHER endpoint (by id).
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/odometer",
                    headers=headers, json={"odometer": 30000})
    assert r.status_code == 409
