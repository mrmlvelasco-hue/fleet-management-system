"""Batch 7 (2026-10-31): Company/Region/Area-District scoping reaches
the Vendor API, the same way branch_ids already does.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import (
    RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.user_management.models import User, Role, Permission


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    role = Role(name="Vendor Org Scope API")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vendor.view", "vendor.create",
                             "vendor.update"])).all()
    user = User(username="venapi", email="venapi@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    region = RegionService().create(code="RG-VENAPI", name="Luzon VenApi")
    area = AreaDistrictService().create(code="AD-VENAPI", name="San Mateo VenApi",
                                        region_id=region.id)
    company = OrgCompanyService().create(short_name="CoVenApi", region_id=region.id,
                                         area_district_id=area.id)
    db.session.commit()
    return user, region, area, company


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "venapi", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_create_vendor_with_company_ids(db, client, env):
    user, region, area, company = env
    t = _token(client)
    r = client.post("/api/v1/vendors", headers=_auth(t), json={
        "code": "VEN-API-CO", "name": "Company API Shop",
        "company_ids": [company.id],
    })
    assert r.status_code == 201
    body = json.loads(r.get_data(as_text=True))
    assert body["company_ids"] == [company.id]


def test_create_vendor_with_region_ids(db, client, env):
    user, region, area, company = env
    t = _token(client)
    r = client.post("/api/v1/vendors", headers=_auth(t), json={
        "code": "VEN-API-RG", "name": "Region API Shop",
        "region_ids": [region.id],
    })
    assert r.status_code == 201
    body = json.loads(r.get_data(as_text=True))
    assert body["region_ids"] == [region.id]


def test_create_vendor_with_area_district_ids(db, client, env):
    user, region, area, company = env
    t = _token(client)
    r = client.post("/api/v1/vendors", headers=_auth(t), json={
        "code": "VEN-API-AD", "name": "Area API Shop",
        "area_district_ids": [area.id],
    })
    assert r.status_code == 201
    body = json.loads(r.get_data(as_text=True))
    assert body["area_district_ids"] == [area.id]


def test_update_vendor_replaces_company_ids(db, client, env):
    user, region, area, company = env
    t = _token(client)
    r = client.post("/api/v1/vendors", headers=_auth(t), json={
        "code": "VEN-API-UPD", "name": "Update API Shop",
        "company_ids": [company.id],
    })
    vendor_id = json.loads(r.get_data(as_text=True))["id"]

    r2 = client.put(f"/api/v1/vendors/{vendor_id}", headers=_auth(t),
                    json={"company_ids": []})
    assert r2.status_code == 200
    assert json.loads(r2.get_data(as_text=True))["company_ids"] == []


def test_vendor_detail_carries_company_region_area_district_labels(
        db, client, env):
    user, region, area, company = env
    t = _token(client)
    r = client.post("/api/v1/vendors", headers=_auth(t), json={
        "code": "VEN-API-LBL", "name": "Label API Shop",
        "company_ids": [company.id], "region_ids": [region.id],
        "area_district_ids": [area.id],
    })
    body = json.loads(r.get_data(as_text=True))
    assert any(c["id"] == company.id for c in body["companies"])
    assert any(rr["id"] == region.id for rr in body["regions"])
    assert any(ad["id"] == area.id for ad in body["area_districts"])
