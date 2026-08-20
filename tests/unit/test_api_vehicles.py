"""Tests for the paged vehicle list endpoint.

Backs the React Vehicle Master list and, by extension, the shared list
scaffold every other Master Data module will use. The contract these
tests pin down -- q / status / type / sort / page / page_size, and a
`total` that reflects the filter rather than the page -- is the contract
those modules will copy, so it is worth getting exactly right once.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


@pytest.fixture()
def veh_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Fleet Viewer")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view"])).all()
    user = User(username="vehuser", email="veh@example.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    nobody = User(username="nobody", email="nobody@example.com",
                  password_hash=hash_password("secret123"), is_active=True)
    nobody.roles = [Role(name="No Access")]
    db.session.add_all([role, user, nobody])

    lv = VehicleTypeService().create(code="LV-V", name="Light", category="LIGHT")
    hv = VehicleTypeService().create(code="HV-V", name="Heavy", category="HEAVY")
    branch = BranchService().create(code="BR-V", name="Vehicle Branch")

    svc = VehicleService()
    svc.create(vehicle_type_id=lv.id, brand="Toyota", model="Hilux", year=2021,
               branch_id=branch.id, conduction_number="V-001",
               plate_number="AAA-1111")
    svc.create(vehicle_type_id=hv.id, brand="Isuzu", model="Elf", year=2020,
               branch_id=branch.id, conduction_number="V-002",
               plate_number="BBB-2222")
    svc.create(vehicle_type_id=lv.id, brand="Kia", model="Pride", year=2019,
               branch_id=branch.id, conduction_number="V-003",
               plate_number="CCC-3333")
    db.session.commit()
    return user, branch, lv, hv


def _token(client, username="vehuser"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Access ──────────────────────────────────────────────────────────────────

def test_requires_a_token(db, client, veh_env):
    assert client.get("/api/v1/vehicles").status_code == 401


def test_requires_vehicle_view(db, client, veh_env):
    token = _token(client, "nobody")
    status, _ = _get(client, "/api/v1/vehicles", token)
    assert status == 403


# ── Shape ───────────────────────────────────────────────────────────────────

def test_returns_the_documented_envelope(db, client, veh_env):
    """The envelope every Master Data list will reuse."""
    token = _token(client)
    status, body = _get(client, "/api/v1/vehicles", token)
    assert status == 200
    assert set(body) >= {"items", "total", "page", "page_size", "pages"}
    for v in body["items"]:
        assert set(v) >= {"id", "plate_number", "conduction_number", "brand",
                          "model", "year", "vehicle_type", "status",
                          "current_odometer", "branch"}


# ── Search ──────────────────────────────────────────────────────────────────

def test_search_matches_plate(db, client, veh_env):
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?q=BBB", token)
    assert [v["plate_number"] for v in body["items"]] == ["BBB-2222"]


def test_search_matches_brand_and_model(db, client, veh_env):
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?q=hilux", token)
    assert len(body["items"]) == 1
    assert body["items"][0]["model"] == "Hilux"


def test_search_matches_conduction_number(db, client, veh_env):
    """A vehicle awaiting plates is findable only by conduction number,
    and those are exactly the ones someone goes looking for."""
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?q=V-003", token)
    assert len(body["items"]) == 1


def test_search_is_case_insensitive(db, client, veh_env):
    token = _token(client)
    _, a = _get(client, "/api/v1/vehicles?q=TOYOTA", token)
    _, b = _get(client, "/api/v1/vehicles?q=toyota", token)
    assert len(a["items"]) == len(b["items"]) == 1


# ── Filters ─────────────────────────────────────────────────────────────────

def test_filter_by_type(db, client, veh_env):
    _, _, lv, _ = veh_env
    token = _token(client)
    _, body = _get(client, f"/api/v1/vehicles?vehicle_type_id={lv.id}", token)
    assert body["total"] == 2


def test_filter_by_status(db, client, veh_env):
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?status=ACTIVE", token)
    assert all(v["status"] == "ACTIVE" for v in body["items"])


def test_unknown_status_is_rejected(db, client, veh_env):
    """A typo'd status silently returning everything would look like the
    filter is broken -- or worse, be mistaken for a real empty result."""
    token = _token(client)
    status, _ = _get(client, "/api/v1/vehicles?status=NONSENSE", token)
    assert status == 400


# ── Paging ──────────────────────────────────────────────────────────────────

def test_total_reflects_the_filter_not_the_page(db, client, veh_env):
    """`total` drives 'Showing 1-2 of N'. If it counted only the page,
    the pager would always claim there is nothing more."""
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?page_size=2", token)
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["pages"] == 2


def test_second_page_returns_the_remainder(db, client, veh_env):
    token = _token(client)
    _, p1 = _get(client, "/api/v1/vehicles?page_size=2&page=1", token)
    _, p2 = _get(client, "/api/v1/vehicles?page_size=2&page=2", token)
    assert len(p2["items"]) == 1
    ids = {v["id"] for v in p1["items"]} | {v["id"] for v in p2["items"]}
    assert len(ids) == 3          # no row appears on both pages


def test_page_beyond_the_end_is_empty_not_an_error(db, client, veh_env):
    token = _token(client)
    status, body = _get(client, "/api/v1/vehicles?page=99", token)
    assert status == 200
    assert body["items"] == []


def test_bad_paging_values_are_rejected(db, client, veh_env):
    token = _token(client)
    assert _get(client, "/api/v1/vehicles?page=abc", token)[0] == 400
    assert _get(client, "/api/v1/vehicles?page_size=0", token)[0] == 400


# ── Sorting ─────────────────────────────────────────────────────────────────

def test_sort_by_plate(db, client, veh_env):
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?sort=plate", token)
    plates = [v["plate_number"] for v in body["items"]]
    assert plates == sorted(plates)


def test_sort_direction_can_be_reversed(db, client, veh_env):
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?sort=plate&dir=desc", token)
    plates = [v["plate_number"] for v in body["items"]]
    assert plates == sorted(plates, reverse=True)


def test_unknown_sort_is_rejected(db, client, veh_env):
    """Falling back to a default silently would leave the user staring at
    a column header that claims to be sorting and isn't."""
    token = _token(client)
    assert _get(client, "/api/v1/vehicles?sort=whatever", token)[0] == 400


# ── Scoping ─────────────────────────────────────────────────────────────────

def test_paging_happens_after_org_scoping(db, client, veh_env):
    """VehicleService.list() applies org scope in PYTHON, after .all().
    Paging in SQL would therefore slice rows the user may not be allowed
    to see, and a scoped-out row would consume a slot on the page --
    producing short pages and a total that disagrees with the rows.
    """
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?page_size=50", token)
    assert body["total"] == len(body["items"])


# ── Enriched list columns ───────────────────────────────────────────────────

def test_rows_carry_assignment_and_due_columns(db, client, veh_env):
    """The list screen shows Assigned To, Next PMS and Registration
    alongside the basics. All three come from the same services the
    dashboard and the Jinja screens use, so a vehicle cannot read
    'overdue' in one place and 'due soon' in another."""
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles", token)
    row = body["items"][0]
    assert set(row) >= {"assigned_driver", "department",
                        "next_pms", "registration"}


def test_due_blocks_are_null_when_nothing_is_scheduled(db, client, veh_env):
    """A vehicle with no PM schedule has no next service. Returning a
    zero-day countdown would render as 'due today' on every such row."""
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles", token)
    row = body["items"][0]
    for block in ("next_pms", "registration"):
        assert row[block] is None or set(row[block]) >= {"status", "days"}


def test_enrichment_is_limited_to_the_page(db, client, veh_env):
    """PM and registration status are expensive per vehicle. Computing
    them for the whole filtered set instead of the 20 rows on screen
    would make page size irrelevant to cost -- the thing paging exists
    to control."""
    token = _token(client)
    _, body = _get(client, "/api/v1/vehicles?page_size=1", token)
    assert len(body["items"]) == 1
    assert body["total"] == 3


def test_summary_counts_describe_the_whole_filtered_set(db, client, veh_env):
    """The stat chips summarise every matching vehicle, not the current
    page -- '12 Active' means twelve in the fleet, not twelve on screen.

    Served by its own endpoint: the due counts cost ~3s across 5,000
    vehicles, twenty times the table itself, so they are not allowed to
    hold the table's request.
    """
    token = _token(client)
    _, s = _get(client, "/api/v1/vehicles/summary", token)
    assert set(s) >= {"total", "active", "in_maintenance",
                      "pms_due_soon", "registration_expiring"}
    assert s["total"] == 3


def test_summary_ignores_paging_but_respects_filters(db, client, veh_env):
    """Filtering to one type must move the chips too; otherwise they
    describe a set the table is no longer showing."""
    _, _, lv, _ = veh_env
    token = _token(client)
    _, all_rows = _get(client, "/api/v1/vehicles/summary", token)
    _, filtered = _get(
        client, f"/api/v1/vehicles/summary?vehicle_type_id={lv.id}", token)
    assert all_rows["total"] == 3
    assert filtered["total"] == 2


# ── SQL-scoped paging path ──────────────────────────────────────────────────

def test_sql_paged_scope_matches_the_python_scope(db, client, veh_env):
    """The fast path must return exactly what the trusted path returns.

    VehicleService.list() filters visibility in Python and is what every
    other screen uses. list_page() reproduces that predicate in SQL so a
    page can be fetched without loading the whole fleet. If the two ever
    disagree, the list screen shows a different fleet from the dashboard
    -- so this asserts equality rather than trusting the rewrite.
    """
    from app.modules.master_data.vehicle.service import VehicleService
    user, _, _, _ = veh_env
    svc = VehicleService()
    slow = {v.id for v in svc.list(user=user)}
    rows, total = svc.list_page(user=user, page=1, page_size=500)
    assert {v.id for v in rows} == slow
    assert total == len(slow)


def test_sql_paged_scope_respects_a_restricted_user(db, client, veh_env):
    """A user scoped to one branch must not see another's vehicles via
    the paged path, which is precisely where a rewritten filter would
    leak."""
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.org.service import BranchService
    from app.modules.user_management.models import User, Role
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    from app.core.security.password import hash_password

    other = BranchService().create(code="BR-OTHER", name="Other Branch")
    scoped = User(username="scoped", email="s@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    scoped.roles = [Role(name="Scoped")]
    db.session.add(scoped)
    db.session.commit()
    UserOrgScopeService().assign(scoped.id, scope_type="BRANCH",
                                 branch_id=other.id)
    db.session.commit()

    svc = VehicleService()
    rows, total = svc.list_page(user=scoped, page=1, page_size=500)
    slow = {v.id for v in svc.list(user=scoped)}
    assert {v.id for v in rows} == slow
    assert total == len(slow)


def test_sql_paged_search_and_sort_match_the_endpoint(db, client, veh_env):
    from app.modules.master_data.vehicle.service import VehicleService
    user, _, _, _ = veh_env
    rows, total = VehicleService().list_page(
        user=user, q="hilux", page=1, page_size=20)
    assert total == 1
    assert rows[0].model == "Hilux"


def test_summary_is_its_own_endpoint(db, client, veh_env):
    """The chips are expensive -- due status across the whole filtered
    set. Kept off the table request so the table is not held behind
    them, exactly as the Jinja dashboard defers its costly figures."""
    token = _token(client)
    status, body = _get(client, "/api/v1/vehicles/summary", token)
    assert status == 200
    assert set(body) >= {"total", "active", "in_maintenance",
                         "pms_due_soon", "registration_expiring"}
