"""Checklist list visibility -- scoped by who is asking.

Reported live: any authenticated user, driver or Fleet officer alike,
saw every vehicle's entire checklist history through GET /checklists.
On the mobile field app this means a driver saw every OTHER driver's
inspections too -- cluttered at best, and a real scope leak at worst.

The fix mirrors a pattern already established elsewhere in this
project (approval task visibility, dashboard awaiting-approval): the
server decides who sees what based on the CALLER's actual permissions,
never a client-supplied flag. A "mine=true" query param a driver could
simply omit would not be a real control.

checklist.review is the signal used to distinguish the two roles, not
a new permission invented for this: it already gates the one action
that makes someone a reviewer rather than a filler-in -- POST
/checklists/:id/submit, the step that finalises the driver's
hand-off. Holding it is already what the rest of this codebase treats
as "this person reviews checklists, not just creates their own."

No prior test file existed for the checklist API at all before this
one.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.vehicle_checklist.service import (
    VehicleChecklistService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    driver_role = Role(name="Driver")
    driver_role.permissions = Permission.query.filter(
        Permission.code.in_(["checklist.view", "checklist.create"])).all()
    reviewer_role = Role(name="Fleet Reviewer")
    reviewer_role.permissions = Permission.query.filter(
        Permission.code.in_(
            ["checklist.view", "checklist.create", "checklist.review",
             "checklist.submit"])
    ).all()

    driver_a = User(username="driver_a", email="da@e.com",
                    password_hash=hash_password("secret123"), is_active=True)
    driver_a.roles = [driver_role]
    driver_b = User(username="driver_b", email="db@e.com",
                    password_hash=hash_password("secret123"), is_active=True)
    driver_b.roles = [driver_role]
    reviewer = User(username="reviewer", email="rv@e.com",
                    password_hash=hash_password("secret123"), is_active=True)
    reviewer.roles = [reviewer_role]
    db.session.add_all([driver_role, reviewer_role, driver_a, driver_b, reviewer])

    branch = BranchService().create(code="BR-CHK", name="Chk Branch")
    vt = VehicleTypeService().create(code="LV-CHK", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2023,
        branch_id=branch.id, conduction_number="CHK-000")
    template = VehicleChecklistService().create_template(name="Daily")
    db.session.commit()
    return {"driver_a": driver_a, "driver_b": driver_b, "reviewer": reviewer,
            "vehicle": vehicle, "template": template}


def _make_checklist(env, user):
    return VehicleChecklistService().create(
        vehicle_id=env["vehicle"].id, template_id=env["template"].id,
        user=user, inspection_date=date.today())


def _token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_a_driver_sees_only_their_own_checklists(db, client, env):
    mine = _make_checklist(env, env["driver_a"])
    _theirs = _make_checklist(env, env["driver_b"])

    status, body = _get(client, "/api/v1/checklists",
                        _token(client, "driver_a"))
    assert status == 200
    ids = {item["id"] for item in body["items"]}
    assert ids == {mine.id}


def test_a_reviewer_sees_every_driver_s_checklists(db, client, env):
    a = _make_checklist(env, env["driver_a"])
    b = _make_checklist(env, env["driver_b"])

    status, body = _get(client, "/api/v1/checklists",
                        _token(client, "reviewer"))
    assert status == 200
    ids = {item["id"] for item in body["items"]}
    assert ids == {a.id, b.id}


def test_a_driver_cannot_widen_their_view_with_query_params(db, client, env):
    """The scope is enforced server-side regardless of what the caller
    asks for -- a driver adding branch_id, or any other existing
    filter, must not see past their own records. If this were
    implemented as an optional filter instead of a mandatory scope, a
    request with no 'mine' flag at all would silently see everyone,
    which is the exact bug being fixed."""
    _mine = _make_checklist(env, env["driver_a"])
    other = _make_checklist(env, env["driver_b"])

    status, body = _get(
        client, f"/api/v1/checklists?branch_id={env['vehicle'].branch_id}",
        _token(client, "driver_a"))
    assert status == 200
    ids = {item["id"] for item in body["items"]}
    assert other.id not in ids


def test_reviewer_s_existing_filters_still_work(db, client, env):
    """Regression guard: adding the scope must not break the filters a
    reviewer already relies on to narrow a fleet-wide list."""
    a = _make_checklist(env, env["driver_a"])
    _b = _make_checklist(env, env["driver_b"])

    status, body = _get(
        client, f"/api/v1/checklists?vehicle_id={env['vehicle'].id}",
        _token(client, "reviewer"))
    assert status == 200
    assert len(body["items"]) == 2  # both are the same vehicle

    status, body = _get(
        client, "/api/v1/checklists?vehicle_id=999999",
        _token(client, "reviewer"))
    assert body["items"] == []


def test_total_reflects_the_same_scope_as_items(db, client, env):
    """`total` drives pagination -- it must count what the caller can
    actually see, not the whole table, or a driver's list would claim
    more pages exist than their own view will ever show."""
    _make_checklist(env, env["driver_a"])
    _make_checklist(env, env["driver_b"])

    _status, body = _get(client, "/api/v1/checklists",
                         _token(client, "driver_a"))
    assert body["total"] == len(body["items"]) == 1
