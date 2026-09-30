"""PMS series generation -- the backend for the "New PMS Profile" page.

One call creates a whole vehicle's service series (e.g. Vios: first
service at 1,000 km, then every 5,000 km to 180,000 km = 37 services)
as linked PMSchedule rows, each with its own optional PMScopeTemplate
checklist -- matching the approved mock-up: "One Save creates every
service and checklist together."

Contract, deliberately:
  - Atomic. If ANY service in the batch fails validation, NOTHING is
    committed -- no partially-created series a fleet admin has to
    notice and clean up by hand.
  - A service's `items` list is genuinely optional. PMScopeTemplate
    already refuses to be created with zero items
    (InvalidScopeError), so a service with none simply gets no scope
    template at all -- exactly the mock-up's "no checklist yet" case,
    which still saves.
  - Every generated schedule shares one maintenance_type_id, and every
    generated scope template is created with that SAME id -- by
    construction, not by re-validating a per-service value the caller
    could get wrong. This is the integrity gap flagged during the
    design pass, closed by never accepting a second, possibly
    inconsistent maintenance_type_id in the first place.
  - sequence_position is assigned 1..N in the order services were
    given, so the series' own order survives a reload.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.maintenance_config.models import PMSchedule, PMScopeTemplate
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.user_management.models import Permission, Role, User

PERMS = ("pmschedule.view", "pmschedule.create", "pmscopetemplate.create")


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    role = Role(name="Series Admin")
    role.permissions = Permission.query.filter(
        Permission.code.in_(PERMS)).all()
    user = User(username="seriesadmin", email="sa@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="Series Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "pmschedule.view").all()
    viewer = User(username="seriesviewer", email="sv@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]

    db.session.add_all([role, user, viewer_role, viewer])
    db.session.commit()

    vt = VehicleTypeService().create(code="LV-S", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-S", name="Preventive",
                                         category="PREVENTIVE")
    brand = VehicleBrandService().create(name="Toyota")
    model = VehicleModelService().create(brand_id=brand.id, name="Vios")
    db.session.commit()
    return {"vt": vt, "mt": mt, "brand": brand, "model": model}


def _token(client, username="seriesadmin"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _post(client, payload, username="seriesadmin"):
    r = client.post("/api/v1/pm-templates/generate-series",
                    json=payload,
                    headers={"Authorization": f"Bearer {_token(client, username)}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _payload(env, services):
    return {
        "vehicle_type_id": env["vt"].id,
        "vehicle_brand_id": env["brand"].id,
        "vehicle_model_id": env["model"].id,
        "maintenance_type_id": env["mt"].id,
        "trigger_mode": "HYBRID",
        "interval_days": 180,
        "profile_code": "VIOS-PMS",
        "services": services,
    }


class TestGenerateSeries:
    def test_creates_one_schedule_per_service_in_order(self, db, client, env):
        status, body = _post(client, _payload(env, [
            {"cumulative_km": 1000},
            {"cumulative_km": 5000},
            {"cumulative_km": 10000},
        ]))
        assert status == 201
        assert len(body["services"]) == 3
        assert [s["cumulative_km"] for s in body["services"]] == [1000, 5000, 10000]
        assert [s["sequence_position"] for s in body["services"]] == [1, 2, 3]

        rows = PMSchedule.query.filter_by(profile_code="VIOS-PMS").order_by(
            PMSchedule.sequence_position).all()
        assert len(rows) == 3
        assert all(r.maintenance_type_id == env["mt"].id for r in rows)
        assert all(r.vehicle_brand_id == env["brand"].id for r in rows)

    def test_a_service_with_items_gets_its_own_linked_checklist(
            self, db, client, env):
        status, body = _post(client, _payload(env, [
            {"cumulative_km": 1000, "items": [
                {"activity_code": "OIL", "activity_description": "Replace engine oil"},
            ]},
        ]))
        assert status == 201
        sched_id = body["services"][0]["id"]
        scope = PMScopeTemplate.query.filter_by(pm_schedule_id=sched_id).first()
        assert scope is not None
        assert len(scope.items) == 1
        assert scope.maintenance_type_id == env["mt"].id  # by construction, never mismatched

    def test_a_service_with_no_items_saves_with_no_scope_template_at_all(
            self, db, client, env):
        status, body = _post(client, _payload(env, [
            {"cumulative_km": 40000},  # no "items" key at all
        ]))
        assert status == 201
        sched_id = body["services"][0]["id"]
        assert PMScopeTemplate.query.filter_by(pm_schedule_id=sched_id).count() == 0

    def test_one_bad_service_rolls_back_the_whole_batch(self, db, client, env):
        before = PMSchedule.query.filter_by(profile_code="VIOS-PMS").count()
        status, body = _post(client, _payload(env, [
            {"cumulative_km": 1000},
            {"cumulative_km": None},  # invalid: HYBRID requires interval_km or cumulative_km
        ]))
        assert status == 400
        after = PMSchedule.query.filter_by(profile_code="VIOS-PMS").count()
        # Nothing from this batch survives -- not even the first, valid one.
        assert after == before

    def test_requires_pmscopetemplate_create_to_attach_a_checklist(
            self, db, client, env):
        # An account that can create schedules but not scope templates
        # must not be able to smuggle a checklist in through this
        # endpoint sideways.
        role = Role(name="Schedule Only")
        role.permissions = Permission.query.filter(
            Permission.code == "pmschedule.create").all()
        user = User(username="scheduleonly", email="so@e.com",
                   password_hash=hash_password("secret123"), is_active=True)
        user.roles = [role]
        db.session.add_all([role, user])
        db.session.commit()

        status, body = _post(client, _payload(env, [
            {"cumulative_km": 1000, "items": [
                {"activity_code": "OIL", "activity_description": "Replace oil"},
            ]},
        ]), username="scheduleonly")
        assert status == 403

    def test_requires_pmschedule_create_permission(self, db, client, env):
        status, _ = _post(client, _payload(env, [{"cumulative_km": 1000}]),
                          username="seriesviewer")
        assert status == 403

    def test_rejects_an_empty_services_list(self, db, client, env):
        status, body = _post(client, _payload(env, []))
        assert status == 400
