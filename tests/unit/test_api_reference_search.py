"""Searchable reference endpoints for form pickers.

Twelve dropdowns in React load their options with a hard page-size cap
and render them into a plain <select>. The ATD form asks for
`/vehicles?page_size=100`, so at 5,000 vehicles it can select 100 of
them. The other 4,900 are unreachable and nothing on screen says so --
the dropdown looks complete, and someone searching for a plate that is
not in the first 100 concludes the vehicle is not enrolled.

That is a correctness fault that grows silently with the client's data,
unlike a missing feature, which is at least visible.

Flask does not have the problem: it uses a search modal plus `api_search`
AJAX endpoints behind Select2. Those are @login_required (session auth)
and unreachable from a bearer-token client, which is why React reached
for a capped list instead.

These endpoints are the bearer-token equivalent. They SEARCH rather than
enumerate, so the cap stops being a cap on what is selectable and
becomes only a cap on how many matches are shown at once.
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
def ref_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Ref Search Role")
    role.permissions = Permission.query.filter(
        Permission.code == "vehicle.view").all()
    user = User(username="refsearch", email="rs@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    branch = BranchService().create(code="BR-RS", name="Search Branch")
    other = BranchService().create(code="BR-RS2", name="Other Branch")
    vt = VehicleTypeService().create(code="LV-RS", name="Light",
                                     category="LIGHT")
    db.session.commit()

    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                            model="Hilux", year=2021, branch_id=branch.id,
                            plate_number="AAA-1111")
    VehicleService().create(vehicle_type_id=vt.id, brand="Isuzu",
                            model="Elf NHR", year=2022, branch_id=other.id,
                            plate_number="ZZZ-9999",
                            conduction_number="CN-777")
    db.session.commit()
    return {"user": user, "branch": branch, "other": other, "vt": vt}


def _token(client, username="refsearch"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Auth ────────────────────────────────────────────────────────────────────

def test_vehicle_search_rejects_anonymous(db, client, ref_env):
    assert client.get("/api/v1/reference/vehicles").status_code == 401


# ── The point of the endpoint ───────────────────────────────────────────────

def test_a_vehicle_beyond_any_page_cap_is_still_findable(db, client, ref_env):
    """The whole reason this exists.

    250 vehicles created after the two fixtures, so the target sorts well
    outside the first 100 the ATD form used to load. Searching for it
    must find it.
    """
    vt, branch = ref_env["vt"], ref_env["branch"]
    for i in range(250):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hiace", year=2020,
                                branch_id=branch.id,
                                plate_number=f"BULK-{i:04d}")
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                            model="Fortuner", year=2023, branch_id=branch.id,
                            plate_number="ZED-0001")
    db.session.commit()

    _status, body = _get(client, "/api/v1/reference/vehicles?q=ZED-0001",
                         _token(client))
    assert [v["plate_number"] for v in body["items"]] == ["ZED-0001"]


def test_search_matches_plate_conduction_brand_and_model(db, client, ref_env):
    """All four, because a user reaches for whichever they have to hand.
    A plate-only search sends someone hunting for a conduction number
    they were handed on paper."""
    t = _token(client)
    for term, expected in (("AAA-1111", "AAA-1111"),
                           ("CN-777", "ZZZ-9999"),
                           ("Isuzu", "ZZZ-9999"),
                           ("Hilux", "AAA-1111")):
        _status, body = _get(client, f"/api/v1/reference/vehicles?q={term}", t)
        assert [v["plate_number"] for v in body["items"]] == [expected], term


def test_no_query_returns_a_first_page_not_nothing(db, client, ref_env):
    """An empty picker on focus reads as "no vehicles enrolled". A first
    page invites typing, and covers the common case of a small fleet
    where the vehicle is right there."""
    _status, body = _get(client, "/api/v1/reference/vehicles", _token(client))
    assert len(body["items"]) == 2


def test_the_label_is_what_the_user_reads_back(db, client, ref_env):
    """Assembled server-side so both apps name a vehicle identically.
    Plate first, because that is what is painted on the vehicle and what
    someone is holding a job sheet for."""
    _status, body = _get(client, "/api/v1/reference/vehicles?q=AAA-1111",
                         _token(client))
    assert body["items"][0]["label"] == "AAA-1111 — Toyota Hilux (2021)"


def test_a_vehicle_with_no_plate_falls_back_to_conduction(db, client,
                                                          ref_env):
    """New vehicles have a conduction number before a plate is issued.
    Labelling them "— Toyota" would make every unplated vehicle look
    identical in the list."""
    vt = ref_env["vt"]
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                            model="Vios", year=2024,
                            branch_id=ref_env["branch"].id,
                            conduction_number="CN-NEW-1")
    db.session.commit()
    _status, body = _get(client, "/api/v1/reference/vehicles?q=CN-NEW-1",
                         _token(client))
    assert body["items"][0]["label"].startswith("CN-NEW-1 —")


def test_results_are_capped_but_the_cap_is_reported(db, client, ref_env):
    """A cap on DISPLAY is fine; a cap that hides its own existence is
    what the old dropdown did. `has_more` lets the picker say "keep
    typing" instead of implying the list is complete."""
    vt, branch = ref_env["vt"], ref_env["branch"]
    for i in range(60):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hiace", year=2020,
                                branch_id=branch.id,
                                plate_number=f"CAP-{i:04d}")
    db.session.commit()
    _status, body = _get(client, "/api/v1/reference/vehicles?q=CAP",
                         _token(client))
    assert len(body["items"]) == 25
    assert body["has_more"] is True


def test_search_respects_org_scope(db, client, ref_env):
    """A picker must not offer a vehicle the user could not open. Scoping
    is VehicleService's, passed the API user."""
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    UserOrgScopeService().assign(ref_env["user"].id, scope_type="BRANCH",
                                 branch_id=ref_env["branch"].id)
    db.session.commit()
    _status, body = _get(client, "/api/v1/reference/vehicles", _token(client))
    plates = [v["plate_number"] for v in body["items"]]
    assert "AAA-1111" in plates
    assert "ZZZ-9999" not in plates


def test_disposed_vehicles_are_not_offered(db, client, ref_env):
    """A disposed vehicle must not be selectable on a new trip ticket or
    maintenance order. It stays visible in the register; that is a
    different question from "may I raise work against it"."""
    vt = ref_env["vt"]
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                            model="Old", year=2005,
                            branch_id=ref_env["branch"].id,
                            plate_number="GONE-001", status="DISPOSED")
    db.session.commit()
    _status, body = _get(client, "/api/v1/reference/vehicles?q=GONE-001",
                         _token(client))
    assert body["items"] == []


# ── Branches and users, the other two Select2 feeds Flask has ───────────────

def test_branches_are_searchable(db, client, ref_env):
    _status, body = _get(client, "/api/v1/reference/branches?q=Other",
                         _token(client))
    assert [b["name"] for b in body["items"]] == ["Other Branch"]


def test_users_are_searchable(db, client, ref_env):
    """Flask's api_search feeds a user picker (notify-on-comment,
    approver override). Inactive users are excluded -- assigning work to
    someone who has left is the same fault as offering a deactivated
    driver."""
    _status, body = _get(client, "/api/v1/reference/users?q=refsearch",
                         _token(client))
    assert [u["username"] for u in body["items"]] == ["refsearch"]
