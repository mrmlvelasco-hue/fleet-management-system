"""Fuel export date-bound and row-count guard (2026-11-09 audit, 2.4).

fuel_export() previously ran `FuelTransaction.query...all()` with NO
date filter at all -- unlike the list endpoint, which accepts
date_from/date_to. At real fleet scale (the audit's own 1,000,000-row
estimate) that is an unbounded in-memory .xlsx build on every request,
the same shape of risk already confirmed and fixed for the Fuel KPI
summary and the Exceptions view on 2026-11-08.

Fix: GET /fuel/export.xlsx now defaults to the trailing 90 days when
the caller gives no date_from/date_to AND does not explicitly ask for
everything (?all=1). An explicit date range is honored exactly as
given, never narrowed by the default. An explicit `all=1` is honored
too, but guarded by a row-count cap so a request for "everything" on a
true million-row table still fails fast with a clear message instead
of building a huge workbook.

The legacy Jinja route (app/modules/transactions/routes.py's own
fuel_export(), reached at the same path through the server-rendered
app rather than the JSON API) had the identical bug and gets the
identical fix, reusing this module's _EXPORT_DEFAULT_DAYS/
_EXPORT_ROW_CAP rather than duplicating the constants -- covered by
TestFuelExportDateBoundJinjaRoute below.
"""
import json
from datetime import datetime, timedelta

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
    branch = BranchService().create(code="BR-FXD", name="Branch FXD")
    vt = VehicleTypeService().create(code="LV-FXD", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="FXD-1")

    role = Role(name="fxd-role")
    role.permissions = Permission.query.all()
    user = User(username="fxduser", email="fxd@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()

    from app.modules.transactions.fuel.models import FuelTransaction
    today = datetime.utcnow()
    recent = FuelTransaction(
        vehicle_id=vehicle.id, transaction_date=today - timedelta(days=5),
        station="Petron Recent", fuel_type="DIESEL", litres=40,
        total_amount=2400)
    old = FuelTransaction(
        vehicle_id=vehicle.id, transaction_date=today - timedelta(days=200),
        station="Petron Old", fuel_type="DIESEL", litres=30,
        total_amount=1800)
    db.session.add_all([recent, old])
    db.session.commit()
    return dict(vehicle=vehicle, recent=recent, old=old)


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "fxduser", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _sheet_text(resp):
    from io import BytesIO
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(resp.data))
    ws = wb.active
    return " ".join(str(c.value) for row in ws.iter_rows(min_row=2)
                   for c in row if c.value is not None)


class TestFuelExportDateBound:
    def test_no_date_params_defaults_to_last_90_days(self, client, db, env):
        hdr = _hdr(client)
        r = client.get("/api/v1/fuel/export.xlsx", headers=hdr)
        assert r.status_code == 200
        text = _sheet_text(r)
        assert "Petron Recent" in text
        assert "Petron Old" not in text

    def test_explicit_date_range_overrides_the_default(self, client, db, env):
        hdr = _hdr(client)
        r = client.get(
            "/api/v1/fuel/export.xlsx?date_from=2025-01-01&date_to=2026-12-31",
            headers=hdr)
        assert r.status_code == 200
        text = _sheet_text(r)
        assert "Petron Recent" in text
        assert "Petron Old" in text

    def test_all_param_bypasses_the_default_window(self, client, db, env):
        hdr = _hdr(client)
        r = client.get("/api/v1/fuel/export.xlsx?all=1", headers=hdr)
        assert r.status_code == 200
        text = _sheet_text(r)
        assert "Petron Recent" in text
        assert "Petron Old" in text

    def test_all_param_still_guarded_by_a_row_count_cap(
            self, client, db, env, monkeypatch):
        import app.modules.api.fuel as fuel_api
        monkeypatch.setattr(fuel_api, "_EXPORT_ROW_CAP", 1)
        hdr = _hdr(client)
        r = client.get("/api/v1/fuel/export.xlsx?all=1", headers=hdr)
        assert r.status_code == 400
        body = json.loads(r.get_data(as_text=True))
        assert "date" in body["message"].lower()

    def test_date_range_is_not_capped_even_if_it_matches_the_cap(
            self, client, db, env, monkeypatch):
        # A caller who narrowed to an explicit range should never be
        # blocked by the cap merely because the cap is also low --
        # the cap exists to stop an unbounded `all=1`, not to second-
        # guess a range the caller already narrowed deliberately.
        import app.modules.api.fuel as fuel_api
        monkeypatch.setattr(fuel_api, "_EXPORT_ROW_CAP", 1)
        hdr = _hdr(client)
        r = client.get(
            "/api/v1/fuel/export.xlsx?date_from=2025-01-01&date_to=2026-12-31",
            headers=hdr)
        assert r.status_code == 200
        text = _sheet_text(r)
        assert "Petron Recent" in text
        assert "Petron Old" in text


def _session_client(client):
    client.post("/login", data={"username": "fxduser", "password": "secret123"},
               follow_redirects=True)
    return client


class TestFuelExportDateBoundJinjaRoute:
    """The legacy server-rendered /transactions/fuel/export.xlsx route
    has its own copy of fuel_export() with the identical bug -- same
    fix, same behavior, reached through session login instead of a
    JWT."""

    def test_no_date_params_defaults_to_last_90_days(self, client, db, env):
        c = _session_client(client)
        r = c.get("/transactions/fuel/export.xlsx")
        assert r.status_code == 200
        text = _sheet_text(r)
        assert "Petron Recent" in text
        assert "Petron Old" not in text

    def test_explicit_date_range_overrides_the_default(self, client, db, env):
        c = _session_client(client)
        r = c.get("/transactions/fuel/export.xlsx"
                  "?date_from=2025-01-01&date_to=2026-12-31")
        assert r.status_code == 200
        text = _sheet_text(r)
        assert "Petron Recent" in text
        assert "Petron Old" in text

    def test_all_param_still_guarded_by_a_row_count_cap(
            self, client, db, env, monkeypatch):
        import app.modules.api.fuel as fuel_api
        monkeypatch.setattr(fuel_api, "_EXPORT_ROW_CAP", 1)
        c = _session_client(client)
        r = c.get("/transactions/fuel/export.xlsx?all=1",
                  follow_redirects=True)
        assert r.status_code == 200
        # The cap redirects back to the fuel list with a flashed
        # warning rather than a JSON error -- this is a server-
        # rendered page, not the API.
        assert b"row limit" in r.data or b"Narrow it" in r.data
