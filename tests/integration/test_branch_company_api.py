"""Stage 3C — Company assignable on the Branch master, via the API.

`Branch.company_id` was added in v277 so that any branch-scoped record
(Driver, Tire, Battery, Vendor…) inherits Company for free through its
existing `branch_id`, instead of every master growing a company column of
its own. The column shipped with NO way to set it: only the Stage 3B
migration tool wrote it, and that tool is blocked on a client mapping
sheet.

Michael confirmed (2026-10-29) that the Branch master is pure legacy and
Company is the real organisational axis going forward. Exposing
`company_id` on the Branch CRUD lets him map his handful of legacy
branches to Companies by hand, today — which in turn lets the dashboard
resolve every existing vehicle's Company through its branch, with no
per-vehicle migration and no mapping sheet on the critical path.
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
    south = RegionService().create(code="SL-BC", name="South Luzon")
    cavite2 = AreaDistrictService().create(
        code="CAV2-BC", name="CAVITE 2", region_id=south.id)
    sl3 = AreaDistrictService().create(
        code="SL3-BC", name="SOUTH LUZON 3", region_id=south.id)
    # Per D2: same short_name, different Area/District -- two real records.
    cavite_co = OrgCompanyService().create(
        short_name="Excellence", region_id=south.id,
        area_district_id=cavite2.id,
        company_name="Excellence Poultery & Livestock Specialist Inc")
    sl3_co = OrgCompanyService().create(
        short_name="Excellence", region_id=south.id,
        area_district_id=sl3.id,
        company_name="Excellence Poultery & Livestock Specialist Inc")
    branch = BranchService().create(code="LEGACY1", name="East Asia Labs")

    role = Role(name="Branch Admin Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["branch.view", "branch.create",
                             "branch.update", "branch.delete"])).all()
    user = User(username="branchadmin", email="branchadmin@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"south": south, "cavite2": cavite2, "sl3": sl3,
            "cavite_co": cavite_co, "sl3_co": sl3_co, "branch": branch}


def _token(client, username="branchadmin"):
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


def _put(client, url, token, payload):
    r = client.put(url, json=payload,
                   headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_branch_json_exposes_company_and_its_region_area(db, client, env):
    """The list/detail payload has to carry the resolved Region and
    Area/District, not just company_id: per D2 the short_name alone is
    ambiguous, so a UI showing only "Excellence" could not tell an
    operator which of the five Companies a branch points at."""
    BranchService().update(env["branch"].id,
                           company_id=env["cavite_co"].id)
    status, body = _get(client, f"/api/v1/branches/{env['branch'].id}",
                        _token(client))
    assert status == 200
    assert body["company_id"] == env["cavite_co"].id
    assert body["company"] == "Excellence"
    assert body["region"] == "South Luzon"
    assert body["area_district"] == "CAVITE 2"


def test_branch_json_company_fields_are_none_when_unmapped(db, client, env):
    """A legacy branch nobody has mapped yet is the normal state, not an
    error -- it must serialize cleanly rather than blowing up on a null
    relationship."""
    status, body = _get(client, f"/api/v1/branches/{env['branch'].id}",
                        _token(client))
    assert status == 200
    assert body["company_id"] is None
    assert body["company"] is None
    assert body["region"] is None
    assert body["area_district"] is None


def test_create_branch_accepts_a_company(db, client, env):
    status, body = _post(client, "/api/v1/branches", _token(client),
                         {"code": "NEWBR", "name": "New Branch",
                          "company_id": env["sl3_co"].id})
    assert status == 201
    assert body["company_id"] == env["sl3_co"].id
    assert body["area_district"] == "SOUTH LUZON 3"


def test_update_branch_assigns_a_company(db, client, env):
    """The main path Michael needs: map an existing legacy branch."""
    status, body = _put(client, f"/api/v1/branches/{env['branch'].id}",
                        _token(client),
                        {"company_id": env["cavite_co"].id})
    assert status == 200
    assert body["company_id"] == env["cavite_co"].id


def test_branch_company_accepts_a_string_id_from_the_browser_form(
        db, client, env):
    """React's SimpleMasterForm sends every field as a JSON string, so
    the id arrives as "7", not 7 -- the same shape that broke Company
    creation (v278). Pinned here so the Branch form can't regress the
    same way."""
    status, body = _put(client, f"/api/v1/branches/{env['branch'].id}",
                        _token(client),
                        {"company_id": str(env["cavite_co"].id)})
    assert status == 200
    assert body["company_id"] == env["cavite_co"].id


def test_branch_company_can_be_cleared(db, client, env):
    """Mapping the wrong Company must be correctable -- sending an empty
    value clears it rather than being silently ignored."""
    BranchService().update(env["branch"].id, company_id=env["cavite_co"].id)
    status, body = _put(client, f"/api/v1/branches/{env['branch'].id}",
                        _token(client), {"company_id": ""})
    assert status == 200
    assert body["company_id"] is None


def test_unknown_company_on_a_branch_is_rejected(db, client, env):
    status, body = _put(client, f"/api/v1/branches/{env['branch'].id}",
                        _token(client), {"company_id": 999999})
    assert status == 400
