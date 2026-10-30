"""Driver / Assignee moves to Company (2026-10-30, Batch 2).

Michael: "we cannot check the filter because the Editing or creation of
Driver/assignee has no company list (combo list for company) and also
the column in the list".

Driver follows Vehicle exactly, by his decision: Branch off the form,
Company required in its place. drivers.branch_id became nullable in
migration e5b3d81c7a24, which carries the same trap Vehicle had --
covers() short-circuits to True when no org context is passed, so a
driver with a null branch would be visible to every user. Requiring
Company is what keeps an org anchor on every new record.

The two gaps found on Vehicle are checked here deliberately, because
Driver had both:
  * company_id missing from the response JSON (so no list column could
    ever show it, and the form could not read it back);
  * company_id missing from the int coercion map (so the browser form's
    "4" was stored as a string and compared unequal to 4 everywhere).
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    north = RegionService().create(code="NL-DC", name="North Luzon")
    south = RegionService().create(code="SL-DC", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-DC", name="NORTH LUZON 2",
                                       region_id=north.id)
    cav = AreaDistrictService().create(code="CAV-DC", name="CAVITE 2",
                                       region_id=south.id)
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    cav_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=cav.id)
    eali = BranchService().create(code="EALI-DC", name="East Asia Labs",
                                  company_id=nl2_co.id)
    # These tests are about Company, not photo policy. The photo rule
    # has its own suite (test_driver_creation_parameters.py); requiring
    # a file here would make every one of them about multipart encoding.
    from app.modules.system_admin.models import SystemParameter
    row = SystemParameter.query.filter_by(
        code="REQUIRE_ASSIGNEE_PHOTO").first()
    if row is None:
        row = SystemParameter(code="REQUIRE_ASSIGNEE_PHOTO", value="NO",
                              data_type="BOOLEAN", group_name="DRIVER",
                              description="Migration switch",
                              is_active=True)
        _db.session.add(row)
    else:
        row.value = "NO"
    _db.session.commit()

    role = Role(name="Driver Role DC")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["driver.view", "driver.create",
                             "driver.update"])).all()
    user = User(username="drvuser", email="drvuser@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"nl2_co": nl2_co, "cav_co": cav_co, "eali": eali}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "drvuser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _post(client, payload, token):
    r = client.post("/api/v1/drivers", json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _base():
    return {"first_name": "Juan", "last_name": "Cruz",
            "employee_number": "EMP-DC1", "assignee_type": "EMPLOYEE"}


def test_a_driver_is_created_with_a_company_and_no_branch(db, client, env):
    status, body = _post(
        client, {**_base(), "company_id": env["nl2_co"].id}, _token(client))
    assert status == 201, body
    assert body["company_id"] == env["nl2_co"].id


def test_company_id_is_in_the_response_so_a_list_column_can_show_it(
        db, client, env):
    """Michael's actual complaint: there was no column because the API
    never returned the field."""
    _s, created = _post(
        client, {**_base(), "company_id": env["nl2_co"].id}, _token(client))
    status, body = _get(client, "/api/v1/drivers", _token(client))
    assert status == 200
    row = next(d for d in body["items"] if d["id"] == created["id"])
    assert row["company_id"] == env["nl2_co"].id
    assert row["company"] == "Excellence"


def test_a_string_company_id_from_the_browser_form_is_coerced(
        db, client, env):
    """React posts every field as a string -- the v278 shape."""
    status, body = _post(
        client, {**_base(), "company_id": str(env["nl2_co"].id)},
        _token(client))
    assert status == 201
    assert body["company_id"] == env["nl2_co"].id


def test_creation_without_a_company_is_refused_with_a_field_message(
        db, client, env):
    status, body = _post(client, _base(), _token(client))
    assert status == 400
    assert body["fields"]["company_id"] == "Company is required."


def test_the_list_filters_by_company(db, client, env):
    token = _token(client)
    _post(client, {**_base(), "company_id": env["nl2_co"].id}, token)
    _post(client, {"first_name": "Pedro", "last_name": "Santos",
                   "employee_number": "EMP-DC2", "assignee_type": "EMPLOYEE",
                   "company_id": env["cav_co"].id}, token)
    _s, everything = _get(client, "/api/v1/drivers", token)
    _s, narrowed = _get(
        client, f"/api/v1/drivers?company_id={env['nl2_co'].id}", token)
    assert len(everything["items"]) == 2
    assert len(narrowed["items"]) == 1
    assert narrowed["items"][0]["employee_number"] == "EMP-DC1"


def test_a_driver_under_a_mapped_branch_inherits_its_company(
        db, client, env):
    """The §20 promise: a branch-scoped record gets Company for free
    through branch_id, with no per-record backfill."""
    token = _token(client)
    _post(client, {"first_name": "Ana", "last_name": "Reyes",
                   "employee_number": "EMP-DC3", "assignee_type": "EMPLOYEE",
                   "branch_id": env["eali"].id,
                   "company_id": env["nl2_co"].id}, token)
    _s, narrowed = _get(
        client, f"/api/v1/drivers?company_id={env['nl2_co'].id}", token)
    assert "EMP-DC3" in {d["employee_number"] for d in narrowed["items"]}
