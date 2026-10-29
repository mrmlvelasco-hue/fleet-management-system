"""Stage 3A — Region / Area-District / Company master-data CRUD API.

Follows the same list/get/create/update/deactivate envelope already
used by /branches, /departments, /vehicle-types (app/modules/api/masters.py),
gated on the same view/create/update/delete permission convention.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.org.service import (
    RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    luzon = RegionService().create(code="LUZ2", name="Luzon")
    san_mateo = AreaDistrictService().create(
        code="SNM2", name="San Mateo", region_id=luzon.id)

    full_role = Role(name="Org Admin Role")
    full_role.permissions = Permission.query.filter(
        Permission.code.in_([
            "region.view", "region.create", "region.update", "region.delete",
            "areadistrict.view", "areadistrict.create",
            "areadistrict.update", "areadistrict.delete",
            "orgcompany.view", "orgcompany.create",
            "orgcompany.update", "orgcompany.delete",
        ])).all()
    full_user = User(username="orgadmin", email="orgadmin@e.com",
                     password_hash=hash_password("secret123"),
                     is_active=True)
    full_user.roles = [full_role]

    view_role = Role(name="Org Viewer Role")
    view_role.permissions = Permission.query.filter(
        Permission.code.in_(
            ["region.view", "areadistrict.view", "orgcompany.view"])).all()
    view_user = User(username="orgviewer", email="orgviewer@e.com",
                     password_hash=hash_password("secret123"),
                     is_active=True)
    view_user.roles = [view_role]

    _db.session.add_all([full_role, full_user, view_role, view_user])
    _db.session.commit()
    return {"luzon": luzon, "san_mateo": san_mateo}


def _token(client, username="orgadmin"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _post(client, url, token, payload):
    r = client.post(url, json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Regions ──────────────────────────────────────────────────────────────

def test_list_regions(db, client, env):
    status, body = _get(client, "/api/v1/regions", _token(client))
    assert status == 200
    codes = {r["code"] for r in body["items"]}
    assert "LUZ2" in codes


def test_create_region(db, client, env):
    status, body = _post(client, "/api/v1/regions", _token(client),
                         {"code": "MIN2", "name": "Mindanao"})
    assert status == 201
    assert body["code"] == "MIN2"


def test_create_region_requires_create_permission(db, client, env):
    status, _ = _post(client, "/api/v1/regions", _token(client, "orgviewer"),
                      {"code": "MIN3", "name": "Mindanao"})
    assert status == 403


def test_duplicate_region_code_is_rejected(db, client, env):
    status, body = _post(client, "/api/v1/regions", _token(client),
                         {"code": "LUZ2", "name": "Dup"})
    assert status == 400


# ── Area / District ──────────────────────────────────────────────────────

def test_list_area_districts_filtered_by_region(db, client, env):
    status, body = _get(
        client, f"/api/v1/area-districts?region_id={env['luzon'].id}",
        _token(client))
    assert status == 200
    names = {a["name"] for a in body["items"]}
    assert "San Mateo" in names


def test_create_area_district(db, client, env):
    status, body = _post(client, "/api/v1/area-districts", _token(client),
                         {"code": "QC2", "name": "Quezon City",
                          "region_id": env["luzon"].id})
    assert status == 201
    assert body["region_id"] == env["luzon"].id


# ── Companies ────────────────────────────────────────────────────────────

def test_create_company(db, client, env):
    status, body = _post(
        client, "/api/v1/org-companies", _token(client),
        {"short_name": "Excellence", "region_id": env["luzon"].id,
         "area_district_id": env["san_mateo"].id,
         "company_name": "Excellence Poultry and Livestock Specialist Inc."})
    assert status == 201
    assert body["short_name"] == "Excellence"
    assert body["region_id"] == env["luzon"].id


def test_create_company_accepts_string_ids_from_the_browser_form(db, client, env):
    # React's SimpleMasterForm keeps every field value as a string, so a
    # real browser POST sends region_id/area_district_id as JSON strings
    # (e.g. "7"), never numbers. This reproduces that exact shape for a
    # genuinely matching Region/Area-District pair, which must succeed.
    status, body = _post(
        client, "/api/v1/org-companies", _token(client),
        {"short_name": "Excellence", "region_id": str(env["luzon"].id),
         "area_district_id": str(env["san_mateo"].id),
         "company_name": "Excellence Poultry and Livestock Specialist Inc."})
    assert status == 201
    assert body["region_id"] == env["luzon"].id


def test_company_mismatched_region_and_area_is_rejected(db, client, env):
    visayas = RegionService().create(code="VIS2", name="Visayas")
    status, body = _post(
        client, "/api/v1/org-companies", _token(client),
        {"short_name": "Bad", "region_id": visayas.id,
         "area_district_id": env["san_mateo"].id})
    assert status == 400


def test_list_companies_can_filter_by_region(db, client, env):
    OrgCompanyService().create(
        short_name="Excellence", region_id=env["luzon"].id,
        area_district_id=env["san_mateo"].id)
    status, body = _get(
        client, f"/api/v1/org-companies?region_id={env['luzon'].id}",
        _token(client))
    assert status == 200
    assert len(body["items"]) == 1
