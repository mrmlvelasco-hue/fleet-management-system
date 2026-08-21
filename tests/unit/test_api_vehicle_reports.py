"""Tests for the Vehicle Register report and the printable vehicle view.

Flask offers both from the vehicle screens: a branch-grouped register
report with an Excel export, and a printable single-vehicle layout.
React inherits both.

The export reuses generate_vehicle_register_xlsx -- the same generator
the Jinja page downloads. Building a second spreadsheet for the API
would mean two files with the same name and different columns, and
whoever received one would have no way to tell which.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


@pytest.fixture()
def rep_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Register Reader")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view",
                             "reportvehicleregister.view"])).all()
    user = User(username="reporter", email="rep@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    # Can see vehicles but holds no report permission.
    plain_role = Role(name="Plain Viewer")
    plain_role.permissions = Permission.query.filter(
        Permission.code == "vehicle.view").all()
    plain = User(username="plainv", email="pv@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    plain.roles = [plain_role]
    db.session.add_all([role, user, plain_role, plain])

    vt = VehicleTypeService().create(code="LV-R", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-R", name="Report Branch")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, conduction_number="R-001",
        plate_number="REP-1111")
    db.session.commit()
    return user, branch


def _token(client, username="reporter"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


# ── Register report ─────────────────────────────────────────────────────────

def test_report_requires_its_own_permission(db, client, rep_env):
    """vehicle.view is not enough. The register report is a separate
    permission in Flask, and a reporting export is a different
    disclosure from browsing one record at a time."""
    token = _token(client, "plainv")
    r = client.get("/api/v1/reports/vehicle-register", headers=_auth(token))
    assert r.status_code == 403


def test_report_is_grouped_by_branch(db, client, rep_env):
    """The legacy VEMS layout is one section per branch. Flattening it
    would change what the report IS, not just how it looks."""
    token = _token(client)
    r = client.get("/api/v1/reports/vehicle-register", headers=_auth(token))
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert isinstance(body["groups"], list)
    group = body["groups"][0]
    # The service's own key is "vehicles"; passed through unchanged
    # rather than renamed, so the API and the Jinja report describe the
    # same structure.
    assert set(group) >= {"branch_code", "branch_name", "vehicles"}
    assert group["vehicles"]


def test_report_reports_when_it_was_generated(db, client, rep_env):
    """A printed register with no timestamp cannot be told apart from
    one printed last quarter."""
    token = _token(client)
    r = client.get("/api/v1/reports/vehicle-register", headers=_auth(token))
    assert json.loads(r.get_data(as_text=True))["generated_at"]


def test_report_follows_org_scope(db, client, rep_env):
    """Rows come from the same service the Jinja report uses, so the
    report cannot show a branch the browser list would hide."""
    token = _token(client)
    r = client.get("/api/v1/reports/vehicle-register", headers=_auth(token))
    body = json.loads(r.get_data(as_text=True))
    from app.modules.master_data.vehicle.report_service import (
        VehicleRegisterReportService)
    user, _ = rep_env
    expected = VehicleRegisterReportService().get_grouped(user=user)
    assert len(body["groups"]) == len(expected)


# ── Excel export ────────────────────────────────────────────────────────────

def test_export_returns_a_spreadsheet(db, client, rep_env):
    token = _token(client)
    r = client.get("/api/v1/reports/vehicle-register/export.xlsx",
                   headers=_auth(token))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("Content-Type", "")
    # A real xlsx is a zip; its first bytes are the local file header.
    assert r.data[:2] == b"PK"


def test_export_sends_a_filename(db, client, rep_env):
    token = _token(client)
    r = client.get("/api/v1/reports/vehicle-register/export.xlsx",
                   headers=_auth(token))
    assert ".xlsx" in r.headers.get("Content-Disposition", "")


def test_export_requires_the_report_permission(db, client, rep_env):
    token = _token(client, "plainv")
    r = client.get("/api/v1/reports/vehicle-register/export.xlsx",
                   headers=_auth(token))
    assert r.status_code == 403


def test_export_respects_a_branch_filter(db, client, rep_env):
    """The download must match what the user is looking at. Exporting
    the whole fleet while the screen shows one branch is the kind of
    mismatch nobody notices until it is already in someone's inbox."""
    _, branch = rep_env
    token = _token(client)
    r = client.get(
        f"/api/v1/reports/vehicle-register/export.xlsx?branch_id={branch.id}",
        headers=_auth(token))
    assert r.status_code == 200
    assert r.data[:2] == b"PK"


# ── Printable vehicle ───────────────────────────────────────────────────────

def test_print_payload_carries_the_record_and_a_timestamp(db, client, rep_env):
    """Print is rendered by the client from the same detail payload, so
    there is no second definition of what a vehicle record contains."""
    from app.modules.master_data.vehicle.models import Vehicle
    vid = Vehicle.query.first().id
    token = _token(client)
    r = client.get(f"/api/v1/vehicles/{vid}/print", headers=_auth(token))
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["vehicle"]["plate_number"] == "REP-1111"
    assert body["generated_at"]
    assert "company" in body


def test_print_follows_vehicle_visibility(db, client, rep_env):
    token = _token(client)
    r = client.get("/api/v1/vehicles/999999/print", headers=_auth(token))
    assert r.status_code == 404


def test_register_report_branch_filter_returns_that_branch(db, client, rep_env):
    """?branch_id= must narrow the report, not empty it.

    The filter compared r["branch_id"] against a key get_rows() never
    emitted, so every group was dropped and any branch-scoped request
    returned an empty register. An empty report reads as "this branch has
    no vehicles" -- a factual claim about the fleet, not a visibly broken
    filter.

    Only export.xlsx had branch_id coverage; the JSON route had none.
    """
    _user, branch = rep_env
    t = _token(client)
    r = client.get(f"/api/v1/reports/vehicle-register?branch_id={branch.id}",
                   headers=_auth(t))
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert len(body["groups"]) == 1
    assert body["groups"][0]["branch_code"] == branch.code
    assert body["groups"][0]["vehicles"][0]["plate_number"] == "REP-1111"


def test_register_report_unknown_branch_is_empty_not_everything(db, client,
                                                                rep_env):
    """A branch with no vehicles returns nothing, rather than falling
    back to the full register -- silently widening a filter shows rows
    the caller believes they excluded."""
    t = _token(client)
    r = client.get("/api/v1/reports/vehicle-register?branch_id=99999",
                   headers=_auth(t))
    assert r.status_code == 200
    assert json.loads(r.get_data(as_text=True))["groups"] == []
