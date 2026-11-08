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


def test_update_department_route_sets_and_returns_company(db, client):
    """Repro for the bug report: user edits a Department's Company in
    the UI, save reports success, but the Departments list still shows
    '--' in the Company column. Exercise the full PUT -> GET round trip
    the UI relies on (not just the service layer) to see whether the
    route itself ever returns the updated company."""
    _authed_user(db)
    co = _company("7")
    token = _token(client, "deptadmin")
    create = client.post("/api/v1/departments",
                         json={"code": "MKT", "name": "Marketing",
                               "company_id": co.id},
                         headers={"Authorization": f"Bearer {token}"})
    dept_id = json.loads(create.get_data(as_text=True))["id"]

    co2 = _company("8")
    r = client.put(f"/api/v1/departments/{dept_id}",
                   json={"company_id": co2.id},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["company_id"] == co2.id
    assert body["company"] == co2.short_name

    # What the Departments list table actually reads after save.
    listing = client.get("/api/v1/departments",
                         headers={"Authorization": f"Bearer {token}"})
    rows = json.loads(listing.get_data(as_text=True))["items"]
    row = next(x for x in rows if x["id"] == dept_id)
    assert row["company_id"] == co2.id
    assert row["company"] == co2.short_name


def test_update_legacy_branch_only_department_to_set_company(db, client):
    """The real data in the bug report: pre-existing departments created
    BEFORE this cutover, with only branch_id set and company_id NULL.
    Confirm assigning a company to one of those (not a freshly-created,
    already-company-mapped department) round-trips correctly too."""
    _authed_user(db)
    branch = BranchService().create(code="LEGACYB", name="Legacy Branch B")
    legacy = DepartmentService().create(
        code="HR-01", name="Human Resources", branch_id=branch.id)
    assert legacy.company_id is None

    co = _company("9")
    token = _token(client, "deptadmin")
    r = client.put(f"/api/v1/departments/{legacy.id}",
                   json={"company_id": co.id},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["company_id"] == co.id
    assert body["company"] == co.short_name

    listing = client.get("/api/v1/departments",
                         headers={"Authorization": f"Bearer {token}"})
    rows = json.loads(listing.get_data(as_text=True))["items"]
    row = next(x for x in rows if x["id"] == legacy.id)
    assert row["company"] == co.short_name


def test_legacy_department_company_display_falls_back_through_branch(db, client):
    """The actual bug report: Michael's 4 live departments are legacy
    rows (branch_id set, company_id still NULL -- never directly
    mapped) and the Departments list showed '--' in the Company column
    for every one of them. Every other converted module (Vehicle,
    Driver, Tire, Battery) resolves the Company for display through
    effective_company_id() -- the record's own company_id, else its
    branch's -- so a legacy record whose BRANCH is mapped to a Company
    still shows that Company instead of a blank. _dept_json() was
    never updated to do the same: it only looks at d.company_id/
    d.company directly, so a legacy, branch-only department shows '--'
    even when its branch has a Company. This currently fails, proving
    the gap.
    """
    _authed_user(db)
    co = _company("10")
    branch = BranchService().create(
        code="LEGACYC", name="Legacy Branch C", company_id=co.id)
    legacy = DepartmentService().create(
        code="RH-01", name="Research", branch_id=branch.id)
    assert legacy.company_id is None  # never directly mapped

    token = _token(client, "deptadmin")
    get_resp = client.get(f"/api/v1/departments/{legacy.id}",
                          headers={"Authorization": f"Bearer {token}"})
    body = json.loads(get_resp.get_data(as_text=True))
    assert body["company"] == co.short_name, (
        "Expected the Company column to fall back through the "
        "department's branch, like Vehicle/Driver/Tire/Battery do, "
        "instead of showing '--' for a legacy record."
    )

    listing = client.get("/api/v1/departments",
                         headers={"Authorization": f"Bearer {token}"})
    rows = json.loads(listing.get_data(as_text=True))["items"]
    row = next(x for x in rows if x["id"] == legacy.id)
    assert row["company"] == co.short_name
    # The raw company_id stays None -- the Edit form must keep showing
    # "-- Not mapped --" rather than silently pretending this
    # department was directly mapped, matching Driver's own contract
    # (d.company_id stays raw; only the *display* field resolves).
    assert row["company_id"] is None
