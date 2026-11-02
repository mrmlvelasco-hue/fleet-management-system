"""/fuel/analysis and /fuel/analysis/export.xlsx -- the Stage 2B Fuel
Analysis report wired up to FuelMonthlyAnalysisService and
export_monthly_analysis_xlsx. Mirrors the org-scope and permission
conventions already proven for /fuel and /fuel/export.xlsx.
"""
import json
from datetime import datetime

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.fuel.models import FuelTransaction
from app.modules.user_management.models import Permission, Role, User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    sync_permissions()
    branch_a = BranchService().create(code="BR-FAN-A", name="Analysis A")
    branch_b = BranchService().create(code="BR-FAN-B", name="Analysis B")
    vt = VehicleTypeService().create(code="LV-FAN", name="Light",
                                     category="LIGHT")
    v_a = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                  model="Vios", year=2023,
                                  branch_id=branch_a.id,
                                  conduction_number="FAN-A-1",
                                  plate_number="FAN-A1")
    v_b = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                  model="Vios", year=2023,
                                  branch_id=branch_b.id,
                                  conduction_number="FAN-B-1",
                                  plate_number="FAN-B1")

    _db.session.add(FuelTransaction(
        vehicle_id=v_a.id, transaction_date=datetime(2026, 1, 5),
        litres=10, total_amount=500, odometer_reported=1000,
        odometer_used=1000, odometer_status="OK",
        reference_number="FAN-A-REF"))
    _db.session.add(FuelTransaction(
        vehicle_id=v_b.id, transaction_date=datetime(2026, 1, 5),
        litres=20, total_amount=1000, odometer_reported=2000,
        odometer_used=2000, odometer_status="OK",
        reference_number="FAN-B-REF"))
    _db.session.commit()

    role = Role(name="Fuel Analysis Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["fuel.view"])).all()
    user = User(username="fuelanalyst", email="fa@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()

    UserOrgScopeService().assign(
        user.id, scope_type="BRANCH", branch_id=branch_a.id)

    return {"branch_a": branch_a, "branch_b": branch_b,
            "v_a": v_a, "v_b": v_b, "user": user}


def _token(client, username="fuelanalyst"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_analysis_endpoint_requires_fuel_view_permission(db, client, env):
    """No Authorization header at all -- the login wall, same shape
    every other fuel endpoint already enforces."""
    r = client.get("/api/v1/fuel/analysis?year=2026")
    assert r.status_code == 401


def test_analysis_returns_rows_and_fleet_summary_for_the_year(
        db, client, env):
    status, body = _get(client, "/api/v1/fuel/analysis?year=2026",
                        _token(client))
    assert status == 200
    assert "rows" in body and "summary" in body
    plates = {r["plate_number"] for r in body["rows"]}
    assert plates == {"FAN-A1"}          # branch-scoped user
    assert len(body["rows"][0]["months"]) == 12
    assert "begin_odometer" in body["rows"][0]["months"][0]
    assert body["summary"]["year"] == 2026


def test_analysis_is_scoped_the_same_way_as_the_list(db, client, env):
    """A branch-scoped user never sees another branch's vehicle, even
    when they pass no branch_id filter at all."""
    status, body = _get(client, "/api/v1/fuel/analysis", _token(client))
    assert status == 200
    plates = {r["plate_number"] for r in body["rows"]}
    assert "FAN-B1" not in plates


def test_analysis_defaults_to_the_current_year_when_not_given(
        db, client, env):
    status, body = _get(client, "/api/v1/fuel/analysis", _token(client))
    assert status == 200
    assert body["summary"]["year"] == datetime.now().year


def test_an_unscoped_global_user_sees_every_vehicle_in_analysis(
        db, client, env):
    sync_permissions()
    role = Role(name="Fuel Analysis Global Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["fuel.view"])).all()
    user = User(username="faglobal", email="fagl@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()

    status, body = _get(client, "/api/v1/fuel/analysis?year=2026",
                        _token(client, "faglobal"))
    assert status == 200
    plates = {r["plate_number"] for r in body["rows"]}
    assert plates == {"FAN-A1", "FAN-B1"}


def test_export_xlsx_is_scoped_and_shaped_like_the_client_workbook(
        db, client, env):
    token = _token(client)
    r = client.get("/api/v1/fuel/analysis/export.xlsx?year=2026",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.mimetype == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    from io import BytesIO
    from openpyxl import load_workbook
    from app.modules.transactions.fuel.monthly_analysis import _MONTH_BLOCK
    wb = load_workbook(BytesIO(r.data))
    ws = wb.active

    # Row 2: vehicle identity headers plus one merged month band per
    # month (JAN..DEC). Row 3: the per-metric sub-headers under each
    # month band. Data starts row 4.
    identity_row = [c.value for c in ws[2]]
    assert "JAN" in identity_row
    assert "DEC" in identity_row
    jan_col = identity_row.index("JAN") + 1
    metric_row = [c.value for c in ws[3]]
    assert metric_row[jan_col - 1: jan_col - 1 + len(_MONTH_BLOCK)] == _MONTH_BLOCK

    plates = [row[2].value for row in ws.iter_rows(min_row=4, max_row=4)]
    assert plates == ["FAN-A1"]
