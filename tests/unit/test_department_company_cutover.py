"""
Stage 3D follow-up (2026-11-08): Department's Branch field is replaced
with Company, the same way Vehicle and Driver were converted on
2026-10-30. Branch is left on the model (nullable now, like
vehicles.branch_id / drivers.branch_id) for legacy records and
history; new departments are created with Company instead.

Verified safe first: Michael ran a collision check
(check_department_company_collision.py) against his real data -- 4
departments, 0 unmapped, 0 (code, effective company) collisions --
before this was implemented.
"""
import pytest

from app.modules.master_data.org.service import (
    BranchService, DepartmentService, OrgCompanyService, RegionService,
    AreaDistrictService, DuplicateCodeError)


def _company(suffix="1"):
    region = RegionService().create(code=f"R{suffix}", name=f"Region {suffix}")
    area = AreaDistrictService().create(
        code=f"A{suffix}", name=f"Area {suffix}", region_id=region.id)
    return OrgCompanyService().create(
        short_name=f"Co{suffix}", region_id=region.id, area_district_id=area.id)


def test_create_department_with_company_and_no_branch(db):
    co = _company()
    dept = DepartmentService().create(
        code="OPS", name="Operations", company_id=co.id)
    assert dept.company_id == co.id
    assert dept.branch_id is None


def test_duplicate_code_rejected_within_the_same_company(db):
    co = _company("2")
    DepartmentService().create(code="HR", name="HR One", company_id=co.id)
    with pytest.raises(DuplicateCodeError):
        DepartmentService().create(code="HR", name="HR Two", company_id=co.id)


def test_same_code_allowed_under_different_companies(db):
    co1 = _company("3")
    co2 = _company("4")
    d1 = DepartmentService().create(code="HR", name="HR One", company_id=co1.id)
    d2 = DepartmentService().create(code="HR", name="HR Two", company_id=co2.id)
    assert d1.id != d2.id


def test_department_still_linkable_to_a_branch_for_legacy_data(db):
    branch = BranchService().create(code="LEGACY1", name="Legacy Branch")
    dept = DepartmentService().create(
        code="OLD", name="Old Department", branch_id=branch.id)
    assert dept.branch_id == branch.id


# ---------- GET /api/v1/departments POST/PUT route-level behavior ----------

import json

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


def _token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _authed_user(db):
    sync_permissions()
    db.session.commit()
    role = Role(name="Dept Admin")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["department.view", "department.create",
                            "department.update"])).all()
    user = User(username="deptadmin", email="deptadmin@example.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()


def test_create_department_route_requires_company_id(db, client):
    _authed_user(db)
    token = _token(client, "deptadmin")
    r = client.post("/api/v1/departments",
                    json={"code": "NOCO", "name": "No Company"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400
    body = json.loads(r.get_data(as_text=True))
    assert "company_id" in body["fields"]


def test_create_department_route_succeeds_with_company_id_only(db, client):
    _authed_user(db)
    co = _company("5")
    token = _token(client, "deptadmin")
    r = client.post("/api/v1/departments",
                    json={"code": "ENG", "name": "Engineering",
                          "company_id": co.id},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    body = json.loads(r.get_data(as_text=True))
    assert body["company_id"] == co.id
    assert body["branch_id"] is None
    assert body["company"] == co.short_name


def test_update_department_route_rejects_blanking_out_company_id(db, client):
    _authed_user(db)
    co = _company("6")
    token = _token(client, "deptadmin")
    create = client.post("/api/v1/departments",
                         json={"code": "FIN", "name": "Finance",
                               "company_id": co.id},
                         headers={"Authorization": f"Bearer {token}"})
    dept_id = json.loads(create.get_data(as_text=True))["id"]
    r = client.put(f"/api/v1/departments/{dept_id}",
                   json={"company_id": ""},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400
