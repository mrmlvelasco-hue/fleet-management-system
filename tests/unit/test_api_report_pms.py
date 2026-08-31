"""PMS Compliance / Due report — JSON and Excel export.

Parity source: report_pms_compliance in system_admin/routes.py. The React
screen replaces the Jinja template; this endpoint must behave as that
route does, because the screen is built against it.

The one thing that is easy to get wrong and expensive when wrong: this
report must NOT run on a bare page open. get_all_due_vehicles evaluates
every active vehicle against every applicable schedule -- one of the
heaviest queries in the system. It runs only on an explicit generate=1
or when a filter is set.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Report Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "reportpmscompliance.view").all()
    viewer = User(username="viewer", email="v@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [role]

    nobody = User(username="nobody", email="n@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    db.session.add_all([role, viewer, nobody])

    branch = BranchService().create(code="BR-RPT", name="Report Branch")
    vt = VehicleTypeService().create(code="LV-RPT", name="Light",
                                     category="LIGHT")
    MaintenanceTypeService().create(code="RPT-MT", name="PMS", category="PM")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="RPT-000")
    db.session.commit()
    return {"viewer": viewer, "branch": branch, "vt": vt}


def _token(client, username="viewer"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, r


# ------------------------------------------------------------- guards

def test_rejects_anonymous(db, client, env):
    assert client.get("/api/v1/reports/pms-compliance").status_code == 401


def test_requires_the_report_permission(db, client, env):
    status, _ = _get(client, "/api/v1/reports/pms-compliance",
                     _token(client, "nobody"))
    assert status == 403


# --------------------------------------------------------- the run gate

def test_does_not_run_on_a_bare_open(db, client, env):
    """No generate, no filters -> rows is null, not an empty list.

    This is the guard that keeps the heaviest query in the system from
    firing every time someone lands on the page. `null` also lets the
    screen tell "not generated yet" from "generated, nothing matched".
    """
    status, r = _get(client, "/api/v1/reports/pms-compliance", _token(client))
    assert status == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["rows"] is None


def test_runs_when_generate_is_set(db, client, env):
    status, r = _get(client, "/api/v1/reports/pms-compliance?generate=1",
                     _token(client))
    assert status == 200
    body = json.loads(r.get_data(as_text=True))
    assert isinstance(body["rows"], list)
    assert body["generated_at"]


def test_runs_when_a_filter_is_set_even_without_generate(db, client, env):
    """A bookmarked filtered URL must keep working."""
    _status, r = _get(
        client, f"/api/v1/reports/pms-compliance?branch_id={env['branch'].id}",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    assert isinstance(body["rows"], list)


# ------------------------------------------------------------- columns

def test_row_shape_matches_the_report_columns(db, client, env):
    _status, r = _get(client, "/api/v1/reports/pms-compliance?generate=1",
                      _token(client))
    body = json.loads(r.get_data(as_text=True))
    if not body["rows"]:
        pytest.skip("no due vehicles in fixture; shape asserted when present")
    row = body["rows"][0]
    for key in ("plate_no", "branch", "cost_center", "make", "model",
                "maintenance_type", "next_due_km", "current_odometer",
                "next_due_date", "status"):
        assert key in row


# ------------------------------------------------------------- export

def test_export_is_an_xlsx_attachment(db, client, env):
    status, r = _get(client, "/api/v1/reports/pms-compliance/export.xlsx",
                     _token(client))
    assert status == 200
    assert "spreadsheetml" in r.headers["Content-Type"]
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.headers["Content-Disposition"].endswith('.xlsx"') or \
        ".xlsx" in r.headers["Content-Disposition"]


def test_export_requires_the_permission_too(db, client, env):
    status, _ = _get(client, "/api/v1/reports/pms-compliance/export.xlsx",
                     _token(client, "nobody"))
    assert status == 403


def test_bad_branch_id_is_a_clean_400(db, client, env):
    status, _ = _get(client, "/api/v1/reports/pms-compliance?branch_id=abc",
                     _token(client))
    assert status == 400
