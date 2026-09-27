"""Vehicle Registration -- new-prefill endpoint.

Mirrors /maintenance-orders/new-prefill exactly: the New Vehicle
Registration form needs to show the applicable checklist BEFORE
creation (matching Flask's own New Vehicle Registration screen, which
resolves and displays the checklist as soon as a vehicle is chosen),
but the only existing checklist-related code ran at CREATE time
(VehicleRegistrationService.create(), via RegistrationTemplateService
.find_applicable()) or toggled an item on an ALREADY-CREATED
registration. Nothing let the form preview the resolved checklist
first. This reuses find_applicable() unchanged -- no second matching
algorithm.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.registration_config.service import RegistrationTemplateService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    branch = BranchService().create(code="BR-REGPRE", name="Reg Prefill Branch")
    vt = VehicleTypeService().create(code="LV-REGPRE", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Kia", model="Picanto", year=2023,
        branch_id=branch.id, conduction_number="REGPRE-000",
        plate_number="ZDM-231")

    role = Role(name="Reg Prefill User")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicleregistration.create"])).all()
    user = User(username="regprefill", email="rp@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    from app.extensions import db as _db
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"branch": branch, "vt": vt, "vehicle": vehicle, "user": user}


def _token(client, username="regprefill"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


class TestNewPrefill:
    def test_returns_the_vehicle_summary(self, db, client, env):
        status, body = _get(
            client,
            f"/api/v1/vehicle-registrations/new-prefill?vehicle_id={env['vehicle'].id}",
            _token(client))
        assert status == 200
        assert body["vehicle"]["plate_number"] == "ZDM-231"
        assert body["vehicle"]["brand"] == "Kia"
        assert body["vehicle"]["model"] == "Picanto"

    def test_returns_branch_and_assignee_matching_the_mo_forms_own_summary(
            self, db, client, env):
        # Same fields, same field-access pattern as
        # /maintenance-orders/new-prefill's vehicle summary -- one
        # "Vehicle Information Summary" panel, reused visually across
        # both forms, must not silently diverge into two different
        # vehicle-summary shapes.
        status, body = _get(
            client,
            f"/api/v1/vehicle-registrations/new-prefill?vehicle_id={env['vehicle'].id}",
            _token(client))
        assert status == 200
        assert body["vehicle"]["branch"] == "Reg Prefill Branch"
        # No assignee/driver was assigned in the fixture -- must be
        # None, not a KeyError or a crash on a null assigned_driver.
        assert body["vehicle"]["assigned_driver"] is None
        assert body["vehicle"]["assigned_driver_position"] is None

    def test_returns_the_matched_templates_checklist(self, db, client, env):
        RegistrationTemplateService().create(
            vehicle_type_id=env["vt"].id, interval_years=3,
            items=[
                {"activity_code": "OR-CR", "activity_description": "Renew OR/CR",
                 "sort_order": 1},
                {"activity_code": "EMISSION", "activity_description": "Emission Test",
                 "sort_order": 2},
            ])
        status, body = _get(
            client,
            f"/api/v1/vehicle-registrations/new-prefill?vehicle_id={env['vehicle'].id}",
            _token(client))
        assert status == 200
        assert len(body["checklist_items"]) == 2
        assert body["checklist_items"][0]["activity_code"] == "OR-CR"
        assert body["checklist_items"][1]["activity_code"] == "EMISSION"

    def test_no_matching_template_returns_an_empty_checklist_not_an_error(
            self, db, client, env):
        status, body = _get(
            client,
            f"/api/v1/vehicle-registrations/new-prefill?vehicle_id={env['vehicle'].id}",
            _token(client))
        assert status == 200
        assert body["checklist_items"] == []

    def test_requires_vehicle_id(self, client, env):
        status, body = _get(
            client, "/api/v1/vehicle-registrations/new-prefill", _token(client))
        assert status == 400

    def test_unknown_vehicle_id_is_a_clean_404_not_a_crash(self, client, env):
        status, body = _get(
            client,
            "/api/v1/vehicle-registrations/new-prefill?vehicle_id=999999",
            _token(client))
        assert status == 404

    def test_requires_permission(self, client, db):
        sync_permissions()
        outsider = User(username="regoutsider", email="ro@e.com",
                        password_hash=hash_password("secret123"), is_active=True)
        from app.extensions import db as _db
        _db.session.add(outsider)
        _db.session.commit()
        status, _ = _get(
            client, "/api/v1/vehicle-registrations/new-prefill?vehicle_id=1",
            _token(client, "regoutsider"))
        assert status == 403
