"""Vehicle Registration Expiry report — JSON and Excel export.

Parity source: report_registration_expiry (system_admin/routes.py).

Unlike PMS Compliance this report has NO run gate: the Flask route runs
it on open. It fetches every status (OVERDUE, DUE_SOON, GOOD, NO_RECORD)
so the status dropdown's options all have data, then narrows by the
chosen status. So `rows` is always a list here, never null.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Reg Report Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "reportregistrationexpiry.view").all()
    viewer = User(username="regviewer", email="rv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [role]
    nobody = User(username="regnobody", email="rn@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    db.session.add_all([role, viewer, nobody])

    branch = BranchService().create(code="BR-REG", name="Reg Branch")
    vt = VehicleTypeService().create(code="LV-REG", name="Light",
                                     category="LIGHT")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2023,
        branch_id=branch.id, conduction_number="REG-000")
    db.session.commit()
    return {"viewer": viewer, "branch": branch, "vt": vt}


def _token(client, username="regviewer"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, r


def test_rejects_anonymous(db, client, env):
    assert client.get(
        "/api/v1/reports/registration-expiry").status_code == 401


def test_requires_the_permission(db, client, env):
    status, _ = _get(client, "/api/v1/reports/registration-expiry",
                     _token(client, "regnobody"))
    assert status == 403


def test_runs_on_open_no_gate(db, client, env):
    """This report has no should_run gate -- rows is always a list."""
    status, r = _get(client, "/api/v1/reports/registration-expiry",
                     _token(client))
    assert status == 200
    body = json.loads(r.get_data(as_text=True))
    assert isinstance(body["rows"], list)
    assert body["generated_at"]


def test_row_shape_matches_the_report_columns(db, client, env):
    _status, r = _get(client, "/api/v1/reports/registration-expiry",
                      _token(client))
    body = json.loads(r.get_data(as_text=True))
    if not body["rows"]:
        pytest.skip("no vehicles in fixture; shape asserted when present")
    row = body["rows"][0]
    for key in ("plate_no", "branch", "make", "model", "lto_month",
                "lto_week", "next_due_date", "source", "status", "warning"):
        assert key in row


def test_status_filter_narrows_results(db, client, env):
    """A recognised status filters; it does not error. The fixture may
    have no rows of a given status, so this asserts the filter is
    accepted and returns a (possibly empty) list rather than 400."""
    _status, r = _get(
        client, "/api/v1/reports/registration-expiry?status=OVERDUE",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    assert isinstance(body["rows"], list)
    assert all(row["status"] == "OVERDUE" for row in body["rows"])


def test_export_is_an_xlsx_attachment(db, client, env):
    status, r = _get(client, "/api/v1/reports/registration-expiry/export.xlsx",
                     _token(client))
    assert status == 200
    assert "spreadsheetml" in r.headers["Content-Type"]
    assert "attachment" in r.headers["Content-Disposition"]


def test_export_requires_the_permission(db, client, env):
    status, _ = _get(client, "/api/v1/reports/registration-expiry/export.xlsx",
                     _token(client, "regnobody"))
    assert status == 403


def test_bad_branch_id_is_a_clean_400(db, client, env):
    status, _ = _get(client, "/api/v1/reports/registration-expiry?branch_id=x",
                     _token(client))
    assert status == 400
