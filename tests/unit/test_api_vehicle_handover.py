"""Vehicle Handover Checklist API -- exercised over real HTTP, not the
service directly, so permission gating and JSON shape are covered too.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def rig(db, app):
    sync_permissions()
    db.session.commit()
    branch = Branch(name="Head Office", code="HO")
    db.session.add(branch)
    db.session.flush()
    vtype = VehicleType(code="CARL", name="Car Light", category="CAR")
    db.session.add(vtype)
    db.session.flush()
    vehicle = Vehicle(plate_number="NAG 1234", brand="Toyota", model="Hilux",
                      year=2022, vehicle_type_id=vtype.id,
                      branch_id=branch.id, is_active=True, status="ACTIVE")
    db.session.add(vehicle)

    full = Role(name="Handover Full")
    full.permissions = Permission.query.filter(
        Permission.code.like("vehiclehandover.%")).all()
    view_only = Role(name="Handover Viewer")
    view_only.permissions = Permission.query.filter_by(
        code="vehiclehandover.view").all()

    officer = User(username="officer", email="o@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    officer.roles = [full]
    viewer = User(username="viewer", email="v@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [view_only]
    db.session.add_all([officer, viewer])
    db.session.commit()
    return {"vehicle": vehicle}


def _login(client, username):
    return client.post("/api/v1/auth/token",
                       json={"username": username, "password": "secret123"})


def _auth_headers(client, username):
    tok = json.loads(_login(client, username).get_data(as_text=True))
    return {"Authorization": f"Bearer {tok['access_token']}"}


class TestPermissionGating:
    def test_view_only_user_cannot_create(self, client, app, rig):
        headers = _auth_headers(client, "viewer")
        resp = client.post("/api/v1/vehicle-handovers",
                           json={"vehicle_id": rig["vehicle"].id},
                           headers=headers)
        assert resp.status_code == 403

    def test_view_only_user_can_list(self, client, app, rig):
        headers = _auth_headers(client, "viewer")
        resp = client.get("/api/v1/vehicle-handovers", headers=headers)
        assert resp.status_code == 200

    def test_full_user_can_create(self, client, app, rig):
        headers = _auth_headers(client, "officer")
        resp = client.post("/api/v1/vehicle-handovers",
                           json={"vehicle_id": rig["vehicle"].id},
                           headers=headers)
        assert resp.status_code == 201
        assert json.loads(resp.get_data(as_text=True))["document_number"]


class TestLifecycleOverHttp:
    def test_full_issue_return_cycle(self, client, app, rig):
        headers = _auth_headers(client, "officer")
        created = client.post("/api/v1/vehicle-handovers",
                              json={"vehicle_id": rig["vehicle"].id},
                              headers=headers)
        hid = json.loads(created.get_data(as_text=True))["id"]

        issued = client.post(f"/api/v1/vehicle-handovers/{hid}/issue",
                             json={"odometer": 125432, "fuel_level": 4},
                             headers=headers)
        assert issued.status_code == 200
        assert json.loads(issued.get_data(as_text=True))["status"] == "ISSUED"

        returned = client.post(f"/api/v1/vehicle-handovers/{hid}/return",
                               json={"odometer": 125600}, headers=headers)
        assert returned.status_code == 200
        assert json.loads(returned.get_data(as_text=True))["status"] == "RETURNED"

    def test_return_before_issue_is_a_client_error_not_a_500(
            self, client, app, rig):
        """A ValueError from the service must surface as 400, not crash
        the request -- checked over real HTTP because that translation
        happens in the route handler, not the service."""
        headers = _auth_headers(client, "officer")
        created = client.post("/api/v1/vehicle-handovers",
                              json={"vehicle_id": rig["vehicle"].id},
                              headers=headers)
        hid = json.loads(created.get_data(as_text=True))["id"]
        resp = client.post(f"/api/v1/vehicle-handovers/{hid}/return",
                           headers=headers)
        assert resp.status_code == 400


class TestReportEndpoint:
    def test_report_shape_is_ready_for_the_react_component(
            self, client, app, rig):
        headers = _auth_headers(client, "officer")
        created = client.post("/api/v1/vehicle-handovers",
                              json={"vehicle_id": rig["vehicle"].id},
                              headers=headers)
        hid = json.loads(created.get_data(as_text=True))["id"]
        resp = client.get(f"/api/v1/vehicle-handovers/{hid}/report",
                          headers=headers)
        body = json.loads(resp.get_data(as_text=True))
        assert resp.status_code == 200
        assert len(body["columns"]) == 3
        assert body["identification"][0]["label"] == "Plate No. / CS No."

    def test_nonexistent_handover_is_404_not_500(self, client, app, rig):
        headers = _auth_headers(client, "officer")
        resp = client.get("/api/v1/vehicle-handovers/999999/report",
                          headers=headers)
        assert resp.status_code == 404
