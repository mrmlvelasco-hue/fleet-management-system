"""Rescue Map backend: vehicle location capture, tracking sessions, the
map list, detail and history (2026-11-17).

Scope rules under test, one per endpoint:

  POST /my/vehicles/<id>/locations      assigned vehicle only, 404 otherwise
  POST /my/vehicles/<id>/tracking/*     assigned vehicle only, 404 otherwise
  POST /vehicles/<id>/locations         MANUAL capture by a user holding
                                        vehiclelocation.view AND .create,
                                        on a vehicle their org scope covers
                                        (the Fleet Coordinator case)
  GET  /rescue-map/vehicles             org scope with .view; otherwise
                                        only the caller's assigned vehicles
  GET  /vehicles/<id>/location[-history] the same rule as the map

Every scope test keeps a second vehicle in the SAME branch held by
someone else: org scope would say yes to it, which is exactly the leak
the assigned-only rule exists to prevent.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.assignment_service import (
    VehicleAssignmentService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.system_admin.models import SystemParameter
from app.modules.user_management.models import Permission, Role, User
from app.modules.vehicle_location.models import (
    VehicleLastLocation, VehicleLocationLog, VehicleTrackingSession)


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes, branch_id=None, restrict=False):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=branch_id, mobile_access=True,
             restrict_to_assigned_vehicles=restrict)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


def _branch_scope(user, branch):
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    UserOrgScopeService().assign(user.id, scope_type="BRANCH",
                                 branch_id=branch.id)
    db.session.commit()


DRIVER = ["vehiclelocation.create"]
COORDINATOR = ["vehiclelocation.view", "vehiclelocation.create"]
VIEWER = ["vehiclelocation.view"]


@pytest.fixture()
def env(app):
    carmona = Branch(code="CAR", name="Carmona")
    cebu = Branch(code="CEB", name="Cebu")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([carmona, cebu, vt])
    db.session.commit()

    juan_u = _user("juan", DRIVER, carmona.id, restrict=True)
    maria_u = _user("maria", DRIVER, carmona.id, restrict=True)
    coord = _user("coord", COORDINATOR, carmona.id)
    _branch_scope(coord, carmona)
    rescue = _user("rescue", VIEWER, carmona.id)
    _branch_scope(rescue, carmona)

    juan = Driver(person_id="PID-1", employee_number="EMP-001",
                  first_name="Juan", last_name="Cruz", phone="0917-000-0001",
                  assignee_type="DRIVER", branch_id=carmona.id)
    maria = Driver(person_id="PID-2", employee_number="EMP-002",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=carmona.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, juan_u.id)
    DriverService().link_user(maria.id, maria_u.id)

    svc = VehicleService()
    mine = svc.create(vehicle_type_id=vt.id, brand="Mitsubishi",
                      model="Triton", year=2026, branch_id=carmona.id,
                      conduction_number="A-001", plate_number="AAK-3766")
    theirs = svc.create(vehicle_type_id=vt.id, brand="Isuzu", model="DMax",
                        year=2023, branch_id=carmona.id,
                        conduction_number="A-002", plate_number="KBH-412")
    far = svc.create(vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
                     year=2024, branch_id=cebu.id,
                     conduction_number="A-003", plate_number="CEB-900")
    db.session.commit()

    assign = VehicleAssignmentService()
    assign.assign(mine.id, juan.id, source="MANUAL")
    assign.assign(theirs.id, maria.id, source="MANUAL")
    return {"carmona": carmona, "cebu": cebu, "juan": juan_u,
            "maria": maria_u, "coord": coord, "rescue": rescue,
            "juan_driver": juan, "mine": mine, "theirs": theirs, "far": far}


def _hdr(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now():
    return datetime.now(timezone.utc)


def _point(**over):
    p = {"client_uuid": str(uuid.uuid4()), "latitude": 14.408132,
         "longitude": 121.041520, "accuracy_m": 3.8,
         "captured_at": _iso(_now() - timedelta(seconds=20)),
         "source": "MANUAL"}
    p.update(over)
    return p


def _send(client, user, vehicle, points, mine=True):
    path = (f"/api/v1/my/vehicles/{vehicle.id}/locations" if mine
            else f"/api/v1/vehicles/{vehicle.id}/locations")
    return client.post(path, json={"points": points},
                       headers=_hdr(client, user))


def _param(code, value, data_type="INTEGER"):
    row = SystemParameter.query.filter_by(code=code).first()
    if row is None:
        db.session.add(SystemParameter(code=code, value=str(value),
                                       data_type=data_type,
                                       group_name="VEHICLE_LOCATION"))
    else:
        row.value = str(value)
    db.session.commit()


# ── Submitting points: the assigned driver ──────────────────────────────────

def test_driver_records_a_manual_point_for_the_assigned_vehicle(
        client, env):
    p = _point(speed_mps=0, heading_deg=90.5, altitude_m=12)
    r = _send(client, "juan", env["mine"], [p])
    body = _body(r)

    assert r.status_code == 200
    assert body["accepted"] == 1
    assert body["results"] == [{"client_uuid": p["client_uuid"],
                                "status": "created"}]
    row = VehicleLocationLog.query.one()
    assert row.vehicle_id == env["mine"].id
    assert row.captured_by == env["juan"].id
    assert float(row.latitude) == pytest.approx(14.408132)
    assert float(row.longitude) == pytest.approx(121.041520)
    assert float(row.accuracy_m) == pytest.approx(3.8)
    assert row.source == "MANUAL"
    assert row.received_at is not None
    assert VehicleLastLocation.query.get(env["mine"].id).log_id == row.id


def test_the_reporting_user_comes_from_the_token_not_the_payload(
        client, env):
    _send(client, "juan", env["mine"],
          [_point(captured_by=env["coord"].id)])
    assert VehicleLocationLog.query.one().captured_by == env["juan"].id


def test_a_vehicle_held_by_someone_else_is_not_found(client, env):
    r = _send(client, "juan", env["theirs"], [_point()])
    assert r.status_code == 404
    assert _body(r) == {"error": "not_found", "message": "Vehicle not found."}
    assert VehicleLocationLog.query.count() == 0


def test_an_unknown_vehicle_answers_exactly_like_someone_elses(client, env):
    r = _send(client, "juan", type("V", (), {"id": 999999})(), [_point()])
    assert r.status_code == 404
    assert _body(r)["message"] == "Vehicle not found."


def test_without_the_create_permission_nothing_can_be_submitted(
        client, env):
    _user("viewer2", VIEWER, env["carmona"].id)
    DriverService().link_user(env["juan_driver"].id,
                              User.query.filter_by(username="viewer2").one().id)
    r = _send(client, "viewer2", env["mine"], [_point()])
    assert r.status_code == 403


def test_a_vehicle_whose_assignment_ended_is_refused(client, env):
    VehicleAssignmentService().release(env["mine"].id)
    r = _send(client, "juan", env["mine"], [_point()])
    assert r.status_code == 404


# ── Idempotency ─────────────────────────────────────────────────────────────

def test_resending_a_point_never_creates_a_second_row(client, env):
    p = _point()
    _send(client, "juan", env["mine"], [p])
    r = _send(client, "juan", env["mine"], [p])
    assert _body(r)["results"] == [{"client_uuid": p["client_uuid"],
                                    "status": "duplicate"}]
    assert _body(r)["duplicates"] == 1
    assert VehicleLocationLog.query.count() == 1


def test_the_same_point_twice_in_one_batch_is_stored_once(client, env):
    p = _point()
    r = _send(client, "juan", env["mine"], [p, dict(p)])
    assert [x["status"] for x in _body(r)["results"]] == [
        "created", "duplicate"]
    assert VehicleLocationLog.query.count() == 1


def test_a_uuid_already_used_for_another_vehicle_is_rejected(client, env):
    p = _point()
    _send(client, "maria", env["theirs"], [p])
    r = _send(client, "juan", env["mine"], [p])
    assert _body(r)["results"][0]["status"] == "rejected"
    assert VehicleLocationLog.query.count() == 1


# ── Validation, point by point ──────────────────────────────────────────────

@pytest.mark.parametrize("over, reason", [
    ({"latitude": 91}, "latitude"),
    ({"latitude": -90.5}, "latitude"),
    ({"longitude": 181}, "longitude"),
    ({"latitude": "abc"}, "latitude"),
    ({"latitude": 0, "longitude": 0}, "0,0"),
    ({"accuracy_m": 0}, "accuracy"),
    ({"accuracy_m": -4}, "accuracy"),
    ({"accuracy_m": None}, "accuracy"),
    ({"source": "GUESS"}, "source"),
    ({"client_uuid": "not-a-uuid"}, "client_uuid"),
    ({"captured_at": None}, "captured_at"),
    ({"captured_at": "yesterday"}, "captured_at"),
    ({"captured_at": "2026-11-17T10:00:00"}, "time zone"),
])
def test_an_invalid_point_is_rejected_with_its_reason(
        client, env, over, reason):
    r = _send(client, "juan", env["mine"], [_point(**over)])
    result = _body(r)["results"][0]
    assert r.status_code == 200
    assert result["status"] == "rejected"
    assert reason in result["reason"]
    assert VehicleLocationLog.query.count() == 0


def test_a_point_from_the_future_is_rejected(client, env):
    r = _send(client, "juan", env["mine"],
              [_point(captured_at=_iso(_now() + timedelta(minutes=10)))])
    assert "future" in _body(r)["results"][0]["reason"]


def test_a_point_older_than_the_backdate_limit_is_rejected(client, env):
    _param("LOCATION_MAX_BACKDATE_HOURS", 72)
    r = _send(client, "juan", env["mine"],
              [_point(captured_at=_iso(_now() - timedelta(hours=73)))])
    assert "older than" in _body(r)["results"][0]["reason"]


def test_good_points_are_kept_when_others_in_the_batch_are_rejected(
        client, env):
    good = _point()
    r = _send(client, "juan", env["mine"], [good, _point(latitude=200)])
    body = _body(r)
    assert (body["accepted"], body["rejected"]) == (1, 1)
    assert VehicleLocationLog.query.one().client_uuid == good["client_uuid"]


def test_the_body_must_carry_a_list_of_points(client, env):
    r = client.post(f"/api/v1/my/vehicles/{env['mine'].id}/locations",
                    json={"points": "x"}, headers=_hdr(client, "juan"))
    assert r.status_code == 400


def test_a_batch_is_limited_to_one_hundred_points(client, env):
    r = _send(client, "juan", env["mine"], [_point() for _ in range(101)])
    assert r.status_code == 400
    assert VehicleLocationLog.query.count() == 0


def test_a_mock_location_is_stored_and_flagged(client, env):
    _send(client, "juan", env["mine"], [_point(is_mocked=True)])
    assert VehicleLocationLog.query.one().is_mocked is True
    r = client.get(f"/api/v1/vehicles/{env['mine'].id}/location",
                   headers=_hdr(client, "coord"))
    assert _body(r)["location"]["is_mocked"] is True


# ── Out-of-order uploads ────────────────────────────────────────────────────

def test_an_older_point_uploaded_later_does_not_replace_the_latest(
        client, env):
    newer = _point(captured_at=_iso(_now() - timedelta(minutes=1)),
                   latitude=14.5)
    older = _point(captured_at=_iso(_now() - timedelta(hours=2)),
                   latitude=14.1)
    _send(client, "juan", env["mine"], [newer])
    _send(client, "juan", env["mine"], [older])
    latest = VehicleLastLocation.query.get(env["mine"].id)
    assert float(latest.log.latitude) == pytest.approx(14.5)
    assert VehicleLocationLog.query.count() == 2


# ── Tracking sessions ───────────────────────────────────────────────────────

def _start(client, vehicle, session_uuid, user="juan"):
    return client.post(f"/api/v1/my/vehicles/{vehicle.id}/tracking/start",
                       json={"client_uuid": session_uuid,
                             "device_id": "dev-1"},
                       headers=_hdr(client, user))


def _stop(client, vehicle, session_uuid, user="juan", **extra):
    return client.post(f"/api/v1/my/vehicles/{vehicle.id}/tracking/stop",
                       json={"client_uuid": session_uuid, **extra},
                       headers=_hdr(client, user))


def test_tracking_start_and_stop(client, env):
    sid = str(uuid.uuid4())
    r = _start(client, env["mine"], sid)
    assert r.status_code == 200
    assert _body(r)["session"]["status"] == "ACTIVE"
    s = VehicleTrackingSession.query.one()
    assert (s.user_id, s.vehicle_id, s.device_id) == (
        env["juan"].id, env["mine"].id, "dev-1")

    r = _stop(client, env["mine"], sid)
    assert _body(r)["session"]["status"] == "STOPPED"
    assert _body(r)["session"]["stop_reason"] == "USER"
    assert s.stopped_at is not None


def test_starting_twice_with_the_same_uuid_is_one_session(client, env):
    sid = str(uuid.uuid4())
    _start(client, env["mine"], sid)
    _start(client, env["mine"], sid)
    assert VehicleTrackingSession.query.count() == 1


def test_a_new_session_closes_the_drivers_previous_one(client, env):
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    _start(client, env["mine"], first)
    _start(client, env["mine"], second)
    old = VehicleTrackingSession.query.filter_by(client_uuid=first).one()
    assert (old.status, old.stop_reason) == ("STOPPED", "REPLACED")


def test_stopping_an_unknown_session_is_not_found(client, env):
    r = _stop(client, env["mine"], str(uuid.uuid4()))
    assert r.status_code == 404


def test_stop_records_a_reason_from_the_phone(client, env):
    sid = str(uuid.uuid4())
    _start(client, env["mine"], sid)
    r = _stop(client, env["mine"], sid, reason="PERMISSION_REVOKED")
    assert _body(r)["session"]["stop_reason"] == "PERMISSION_REVOKED"


def test_tracking_another_drivers_vehicle_is_not_found(client, env):
    r = _start(client, env["theirs"], str(uuid.uuid4()))
    assert r.status_code == 404
    assert VehicleTrackingSession.query.count() == 0


def test_an_automatic_point_needs_a_session(client, env):
    r = _send(client, "juan", env["mine"], [_point(source="AUTOMATIC")])
    assert "session" in _body(r)["results"][0]["reason"]


def test_an_automatic_point_for_a_session_not_yet_started_creates_it(
        client, env):
    """Tracking started while offline: the start call and the points are
    queued together and may arrive in either order."""
    sid = str(uuid.uuid4())
    captured = _now() - timedelta(minutes=3)
    r = _send(client, "juan", env["mine"],
              [_point(source="AUTOMATIC", session_uuid=sid,
                      captured_at=_iso(captured))])
    assert _body(r)["accepted"] == 1
    s = VehicleTrackingSession.query.one()
    assert s.client_uuid == sid and s.status == "ACTIVE"
    assert VehicleLocationLog.query.one().tracking_session_id == s.id


def test_points_captured_before_a_stop_are_accepted_after_it(client, env):
    """Offline: the stop reaches the server before the queued points."""
    sid = str(uuid.uuid4())
    _start(client, env["mine"], sid)
    captured = _now() - timedelta(seconds=30)
    _stop(client, env["mine"], sid)
    r = _send(client, "juan", env["mine"],
              [_point(source="AUTOMATIC", session_uuid=sid,
                      captured_at=_iso(captured))])
    assert _body(r)["accepted"] == 1


def test_a_point_captured_well_after_its_session_stopped_is_rejected(
        client, env):
    sid = str(uuid.uuid4())
    _start(client, env["mine"], sid)
    s = VehicleTrackingSession.query.one()
    s.stopped_at = (_now() - timedelta(hours=1)).replace(tzinfo=None)
    s.status = "STOPPED"
    db.session.commit()
    r = _send(client, "juan", env["mine"],
              [_point(source="AUTOMATIC", session_uuid=sid)])
    assert "stopped" in _body(r)["results"][0]["reason"]


def test_a_session_of_another_vehicle_cannot_be_used(client, env):
    sid = str(uuid.uuid4())
    _start(client, env["theirs"], sid, user="maria")
    r = _send(client, "juan", env["mine"],
              [_point(source="AUTOMATIC", session_uuid=sid)])
    assert _body(r)["results"][0]["status"] == "rejected"


def test_points_move_the_sessions_last_update(client, env):
    sid = str(uuid.uuid4())
    _start(client, env["mine"], sid)
    _send(client, "juan", env["mine"],
          [_point(source="AUTOMATIC", session_uuid=sid)])
    assert VehicleTrackingSession.query.one().last_update_at is not None


# ── Fleet Coordinator manual capture ────────────────────────────────────────

def test_a_coordinator_captures_a_vehicle_in_scope(client, env):
    r = _send(client, "coord", env["theirs"], [_point()], mine=False)
    assert _body(r)["accepted"] == 1
    row = VehicleLocationLog.query.one()
    assert (row.vehicle_id, row.captured_by, row.source) == (
        env["theirs"].id, env["coord"].id, "MANUAL")


def test_a_coordinator_cannot_send_automatic_points(client, env):
    r = _send(client, "coord", env["theirs"],
              [_point(source="AUTOMATIC", session_uuid=str(uuid.uuid4()))],
              mine=False)
    assert _body(r)["results"][0]["status"] == "rejected"
    assert "MANUAL" in _body(r)["results"][0]["reason"]


def test_a_coordinator_cannot_capture_outside_scope(client, env):
    r = _send(client, "coord", env["far"], [_point()], mine=False)
    assert r.status_code == 404


def test_a_driver_cannot_use_the_coordinator_route(client, env):
    r = _send(client, "juan", env["theirs"], [_point()], mine=False)
    assert r.status_code == 403


def test_view_alone_cannot_capture(client, env):
    r = _send(client, "rescue", env["theirs"], [_point()], mine=False)
    assert r.status_code == 403


def test_a_user_restricted_to_assigned_vehicles_cannot_capture_others(
        client, env):
    u = _user("restricted", COORDINATOR, env["carmona"].id, restrict=True)
    _branch_scope(u, env["carmona"])
    r = _send(client, "restricted", env["theirs"], [_point()], mine=False)
    assert r.status_code == 404


# ── Statuses ────────────────────────────────────────────────────────────────

def _vehicle_row(client, user, vehicle):
    r = client.get("/api/v1/rescue-map/vehicles", headers=_hdr(client, user))
    rows = {v["id"]: v for v in _body(r)["items"]}
    return rows.get(vehicle.id)


@pytest.mark.parametrize("minutes_ago, status", [
    (2, "LIVE"), (30, "LAST_REPORTED"), (180, "STALE")])
def test_status_follows_the_capture_time(client, env, minutes_ago, status):
    _param("LOCATION_LIVE_MINUTES", 15)
    _param("LOCATION_STALE_MINUTES", 60)
    _send(client, "juan", env["mine"],
          [_point(captured_at=_iso(_now() - timedelta(minutes=minutes_ago)))])
    assert _vehicle_row(client, "coord", env["mine"])["status"] == status


def test_a_vehicle_without_points_is_unavailable(client, env):
    row = _vehicle_row(client, "coord", env["theirs"])
    assert row["status"] == "UNAVAILABLE"
    assert row["location"] is None


def test_an_old_point_uploaded_just_now_is_not_live(client, env):
    """Live is decided on when the phone CAPTURED the point, never on
    when the server received it."""
    _param("LOCATION_LIVE_MINUTES", 15)
    _param("LOCATION_STALE_MINUTES", 60)
    _send(client, "juan", env["mine"],
          [_point(captured_at=_iso(_now() - timedelta(hours=3)))])
    assert _vehicle_row(client, "coord", env["mine"])["status"] == "STALE"


# ── The map list ────────────────────────────────────────────────────────────

def test_the_coordinator_sees_every_vehicle_in_scope_and_nothing_else(
        client, env):
    r = client.get("/api/v1/rescue-map/vehicles", headers=_hdr(client, "coord"))
    ids = {v["id"] for v in _body(r)["items"]}
    assert ids == {env["mine"].id, env["theirs"].id}


def test_a_driver_without_view_sees_only_the_assigned_vehicle(client, env):
    r = client.get("/api/v1/rescue-map/vehicles", headers=_hdr(client, "juan"))
    assert [v["id"] for v in _body(r)["items"]] == [env["mine"].id]


def test_a_restricted_user_with_view_still_sees_only_assigned(client, env):
    u = _user("rv", VIEWER, env["carmona"].id, restrict=True)
    DriverService().link_user(env["juan_driver"].id, u.id)
    r = client.get("/api/v1/rescue-map/vehicles", headers=_hdr(client, "rv"))
    assert [v["id"] for v in _body(r)["items"]] == [env["mine"].id]


def test_the_map_needs_one_of_the_location_permissions(client, env):
    _user("nobody", ["vehicle.view"], env["carmona"].id)
    r = client.get("/api/v1/rescue-map/vehicles", headers=_hdr(client, "nobody"))
    assert r.status_code == 403


def test_the_map_row_carries_what_the_sheet_shows(client, env):
    _send(client, "juan", env["mine"], [_point()])
    row = _vehicle_row(client, "coord", env["mine"])
    assert row["plate_number"] == "AAK-3766"
    assert (row["brand"], row["model"]) == ("Mitsubishi", "Triton")
    assert row["vehicle_type"] == "Light"
    assert row["branch"] == "Carmona"
    assert row["assignee"]["name"] == "Juan Cruz"
    assert row["assignee"]["employee_number"] == "EMP-001"
    loc = row["location"]
    assert loc["latitude"] == pytest.approx(14.408132)
    assert loc["accuracy_m"] == pytest.approx(3.8)
    assert loc["source"] == "MANUAL"
    assert loc["captured_by"]["name"] == "juan"
    assert loc["captured_at"].endswith("Z")
    assert loc["received_at"].endswith("Z")


def test_the_map_counts_each_status(client, env):
    _param("LOCATION_LIVE_MINUTES", 15)
    _send(client, "juan", env["mine"], [_point()])
    r = client.get("/api/v1/rescue-map/vehicles", headers=_hdr(client, "coord"))
    assert _body(r)["counts"] == {"ALL": 2, "LIVE": 1, "LAST_REPORTED": 0,
                                  "STALE": 0, "UNAVAILABLE": 1}


def test_the_map_filters_by_status_and_search(client, env):
    _send(client, "juan", env["mine"], [_point()])
    h = _hdr(client, "coord")
    live = _body(client.get("/api/v1/rescue-map/vehicles?status=LIVE",
                            headers=h))["items"]
    assert [v["id"] for v in live] == [env["mine"].id]
    found = _body(client.get("/api/v1/rescue-map/vehicles?q=KBH",
                             headers=h))["items"]
    assert [v["id"] for v in found] == [env["theirs"].id]


def test_an_active_tracking_session_shows_on_the_map(client, env):
    _start(client, env["mine"], str(uuid.uuid4()))
    row = _vehicle_row(client, "coord", env["mine"])
    assert row["tracking"]["status"] == "ACTIVE"
    assert row["tracking"]["started_at"].endswith("Z")


# ── Detail and history ──────────────────────────────────────────────────────

def test_detail_includes_the_driver_contact_for_viewers(client, env):
    _send(client, "juan", env["mine"], [_point()])
    r = client.get(f"/api/v1/vehicles/{env['mine'].id}/location",
                   headers=_hdr(client, "rescue"))
    body = _body(r)
    assert r.status_code == 200
    assert body["vehicle"]["assignee"]["phone"] == "0917-000-0001"
    assert body["status"] == "LIVE"


def test_detail_outside_scope_is_not_found(client, env):
    r = client.get(f"/api/v1/vehicles/{env['far'].id}/location",
                   headers=_hdr(client, "coord"))
    assert r.status_code == 404


def test_a_driver_reads_the_own_vehicles_detail_but_no_other(client, env):
    h = _hdr(client, "juan")
    assert client.get(f"/api/v1/vehicles/{env['mine'].id}/location",
                      headers=h).status_code == 200
    assert client.get(f"/api/v1/vehicles/{env['theirs'].id}/location",
                      headers=h).status_code == 404


def test_history_is_newest_first_with_the_reporter(client, env):
    t = _now()
    _send(client, "juan", env["mine"], [
        _point(captured_at=_iso(t - timedelta(minutes=10)), latitude=14.1),
        _point(captured_at=_iso(t - timedelta(minutes=2)), latitude=14.2)])
    r = client.get(f"/api/v1/vehicles/{env['mine'].id}/location-history",
                   headers=_hdr(client, "coord"))
    items = _body(r)["items"]
    assert [i["latitude"] for i in items] == [
        pytest.approx(14.2), pytest.approx(14.1)]
    assert items[0]["captured_by"] == {"id": env["juan"].id, "name": "juan"}
    assert _body(r)["total"] == 2


def test_history_filters_by_source_and_pages(client, env):
    sid = str(uuid.uuid4())
    _start(client, env["mine"], sid)
    _send(client, "juan", env["mine"],
          [_point()] + [_point(source="AUTOMATIC", session_uuid=sid)
                        for _ in range(3)])
    h = _hdr(client, "coord")
    base = f"/api/v1/vehicles/{env['mine'].id}/location-history"
    manual = _body(client.get(f"{base}?source=MANUAL", headers=h))
    assert manual["total"] == 1
    page = _body(client.get(f"{base}?per_page=2&page=2", headers=h))
    assert (page["total"], len(page["items"])) == (4, 2)


def test_history_outside_scope_is_not_found(client, env):
    r = client.get(f"/api/v1/vehicles/{env['theirs'].id}/location-history",
                   headers=_hdr(client, "juan"))
    assert r.status_code == 404


# ── Settings for the app ────────────────────────────────────────────────────

def test_settings_carry_the_parameters_and_what_this_user_may_do(
        client, env):
    from app.cli import _seed_system_parameters
    _seed_system_parameters()
    db.session.commit()
    r = client.get("/api/v1/location/settings", headers=_hdr(client, "juan"))
    s = _body(r)
    assert r.status_code == 200
    assert s["tracking_interval_seconds"] == 120
    assert s["tracking_distance_m"] == 50
    assert s["stationary_heartbeat_seconds"] == 600
    assert s["max_accuracy_m"] == 100
    assert s["live_minutes"] == 15 and s["stale_minutes"] == 60
    assert s["map_provider"] == "GOOGLE"
    assert (s["map_tile_url"], s["map_attribution"]) == ("", "")
    assert not any("key" in k.lower() for k in s)
    assert s["tracking_start_mode"] == "MANUAL"
    assert s["driver_can_stop_tracking"] is True
    assert s["tracking_notice_enabled"] is True
    assert len(s["tracking_notice_text"]) > 20
    assert s["can_view_fleet"] is False
    assert s["can_capture"] is True
    assert s["can_capture_in_scope"] is False
    assert s["assigned_vehicle_ids"] == [env["mine"].id]


def test_settings_for_a_coordinator(client, env):
    s = _body(client.get("/api/v1/location/settings",
                         headers=_hdr(client, "coord")))
    assert (s["can_view_fleet"], s["can_capture"],
            s["can_capture_in_scope"]) == (True, True, True)


def test_settings_fall_back_to_defaults_without_parameter_rows(client, env):
    s = _body(client.get("/api/v1/location/settings",
                         headers=_hdr(client, "juan")))
    assert s["tracking_interval_seconds"] == 120
    assert s["tracking_start_mode"] == "MANUAL"


def test_the_tile_provider_is_used_only_with_a_tile_address(client, env):
    _param("LOCATION_MAP_PROVIDER", "TILES", "STRING")
    s = _body(client.get("/api/v1/location/settings",
                         headers=_hdr(client, "juan")))
    assert s["map_provider"] == "GOOGLE"
    _param("LOCATION_MAP_TILE_URL", "https://tiles.example.com/{z}/{x}/{y}.png",
           "STRING")
    s = _body(client.get("/api/v1/location/settings",
                         headers=_hdr(client, "juan")))
    assert s["map_provider"] == "TILES"
    assert s["map_tile_url"] == "https://tiles.example.com/{z}/{x}/{y}.png"


def test_an_unknown_map_provider_falls_back_to_google(client, env):
    _param("LOCATION_MAP_PROVIDER", "BING", "STRING")
    s = _body(client.get("/api/v1/location/settings",
                         headers=_hdr(client, "juan")))
    assert s["map_provider"] == "GOOGLE"


def test_an_unrecognised_start_mode_falls_back_to_manual(client, env):
    _param("LOCATION_TRACKING_START_MODE", "ALWAYS", "STRING")
    s = _body(client.get("/api/v1/location/settings",
                         headers=_hdr(client, "juan")))
    assert s["tracking_start_mode"] == "MANUAL"


# ── Retention ───────────────────────────────────────────────────────────────

def test_purge_removes_old_history_but_keeps_each_vehicles_latest(
        app, client, env):
    from app.modules.vehicle_location.service import VehicleLocationService
    first, second = _point(captured_at=_iso(_now() - timedelta(hours=2))), \
        _point(captured_at=_iso(_now() - timedelta(hours=1)))
    _send(client, "juan", env["mine"], [first, second])
    # Age both past the retention period; the second stays the latest.
    for row, days in ((first, 200), (second, 150)):
        log = VehicleLocationLog.query.filter_by(
            client_uuid=row["client_uuid"]).one()
        log.captured_at = log.captured_at - timedelta(days=days)
    db.session.commit()

    deleted = VehicleLocationService().purge(retention_days=90)

    assert deleted == 1
    remaining = VehicleLocationLog.query.one()
    assert remaining.client_uuid == second["client_uuid"]
    assert VehicleLastLocation.query.get(env["mine"].id).log_id == \
        remaining.id


def test_purge_with_dry_run_deletes_nothing(app, client, env):
    from app.modules.vehicle_location.service import VehicleLocationService
    _send(client, "juan", env["mine"],
          [_point(captured_at=_iso(_now() - timedelta(hours=1)))])
    _send(client, "juan", env["mine"], [_point()])
    for row in VehicleLocationLog.query.all():
        row.captured_at = row.captured_at - timedelta(days=200)
    db.session.commit()
    assert VehicleLocationService().purge(retention_days=90,
                                          dry_run=True) == 1
    assert VehicleLocationLog.query.count() == 2


# ── Seed and permissions ────────────────────────────────────────────────────

def test_the_seed_adds_every_location_parameter(app, env):
    from app.cli import _seed_system_parameters
    _seed_system_parameters()
    db.session.commit()
    codes = {p.code for p in SystemParameter.query.filter_by(
        group_name="VEHICLE_LOCATION").all()}
    assert codes == {
        "LOCATION_TRACKING_INTERVAL_SECONDS", "LOCATION_TRACKING_DISTANCE_M",
        "LOCATION_STATIONARY_HEARTBEAT_SECONDS", "LOCATION_MAX_ACCURACY_M",
        "LOCATION_LIVE_MINUTES", "LOCATION_STALE_MINUTES",
        "LOCATION_MAX_BACKDATE_HOURS", "LOCATION_HISTORY_RETENTION_DAYS",
        "LOCATION_MAP_PROVIDER", "LOCATION_MAP_TILE_URL",
        "LOCATION_MAP_ATTRIBUTION",
        "LOCATION_TRACKING_START_MODE", "LOCATION_DRIVER_CAN_STOP_TRACKING",
        "LOCATION_TRACKING_NOTICE_ENABLED", "LOCATION_TRACKING_NOTICE_TEXT"}
    for p in SystemParameter.query.filter_by(
            group_name="VEHICLE_LOCATION").all():
        assert p.description and len(p.description) <= 255


def test_the_two_permission_codes_are_registered(app):
    from app.core.security.registry import registry
    codes = {d.code for d in registry.definitions}
    assert {"vehiclelocation.view", "vehiclelocation.create"} <= codes
    assert not {c for c in codes if c.startswith("vehiclelocation.")
                and c.split(".")[1] not in
                ("view", "create", "update", "delete", "print")}


def test_the_purge_command_reports_what_it_did(app, env):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["location", "purge", "--dry-run"])
    assert result.exit_code == 0
    assert "Would delete 0 location point(s)." in result.output
    result = runner.invoke(args=["location", "purge", "--days", "30"])
    assert "Deleted 0 location point(s)." in result.output
