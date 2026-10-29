"""Stage 3B: POST /org-companies/migrate-branches -- upload a Branch ->
Company mapping sheet, dry-run (default) or apply (commit=1), mirroring
the existing /fuel/import upload convention.
"""
import json
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService)
from app.modules.master_data.org.models import OrgCompany
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


def _xlsx_bytes(rows, header=("branch_code", "company_short_name",
                              "company_name", "region_code",
                              "area_district_code")):
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


@pytest.fixture()
def env(db):
    sync_permissions()
    branch = BranchService().create(code="MIGAPI-1", name="Migrate API Branch")
    vt = VehicleTypeService().create(code="LV-MIGAPI", name="Light",
                                     category="LIGHT")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="MIGAPI-V1")
    region = RegionService().create(code="RG-MIGAPI", name="Region MigApi")
    AreaDistrictService().create(code="AD-MIGAPI", name="Area MigApi",
                                 region_id=region.id)

    role = Role(name="Org Migration Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["orgcompany.create"])).all()
    user = User(username="migadmin", email="migadmin@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    no_perm_role = Role(name="No Perm Role")
    no_perm_user = User(username="noperm", email="noperm@e.com",
                        password_hash=hash_password("secret123"),
                        is_active=True)
    no_perm_user.roles = [no_perm_role]

    _db.session.add_all([role, user, no_perm_role, no_perm_user])
    _db.session.commit()
    return branch


def _token(client, username="migadmin"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def test_dry_run_upload_previews_without_writing(db, client, env):
    token = _token(client)
    content = _xlsx_bytes([("MIGAPI-1", "Migrate API Co", None,
                            "RG-MIGAPI", "AD-MIGAPI")])
    r = client.post(
        "/api/v1/org-companies/migrate-branches",
        data={"file": (BytesIO(content), "mapping.xlsx")},
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"})
    body = json.loads(r.get_data(as_text=True))
    assert r.status_code == 200
    assert body["dry_run"] is True
    assert body["applied"] is False
    assert len(body["companies_created"]) == 1
    assert OrgCompany.query.count() == 0


def test_commit_1_applies_the_mapping(db, client, env):
    token = _token(client)
    content = _xlsx_bytes([("MIGAPI-1", "Migrate API Co", None,
                            "RG-MIGAPI", "AD-MIGAPI")])
    r = client.post(
        "/api/v1/org-companies/migrate-branches",
        data={"file": (BytesIO(content), "mapping.xlsx"), "commit": "1"},
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"})
    body = json.loads(r.get_data(as_text=True))
    assert r.status_code == 200
    assert body["applied"] is True
    assert OrgCompany.query.filter_by(short_name="Migrate API Co").count() == 1


def test_requires_orgcompany_create_permission(db, client, env):
    token = _token(client, "noperm")
    content = _xlsx_bytes([("MIGAPI-1", "Migrate API Co", None,
                            "RG-MIGAPI", "AD-MIGAPI")])
    r = client.post(
        "/api/v1/org-companies/migrate-branches",
        data={"file": (BytesIO(content), "mapping.xlsx")},
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_no_file_is_a_400_not_a_500(db, client, env):
    token = _token(client)
    r = client.post(
        "/api/v1/org-companies/migrate-branches",
        data={}, content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400
