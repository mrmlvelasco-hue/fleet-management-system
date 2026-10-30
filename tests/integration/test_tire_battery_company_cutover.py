"""Tire / Battery move to Company (2026-10-30, Batch 3).

Michael: "we cannot check the filter because the Editing or creation of
tire has no company list (combo list for company) and also the column
in the list and replace the branches by the company." Tire and Battery
get the same treatment as Vehicle and Driver, by his decision (confirmed
2026-10-30): Branch off both forms, Company required in its place.

tires.branch_id / batteries.branch_id were already nullable before this
batch (Stage 3D), so no migration here -- just the same gaps Vehicle and
Driver had: company_id missing from the response (no list column could
ever show it, and the form could not read it back) and from the int
coercion map.
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
    north = RegionService().create(code="NL-TB", name="North Luzon")
    south = RegionService().create(code="SL-TB", name="South Luzon")
    nl2 = AreaDistrictService().create(code="NL2-TB", name="NORTH LUZON 2",
                                       region_id=north.id)
    cav = AreaDistrictService().create(code="CAV-TB", name="CAVITE 2",
                                       region_id=south.id)
    nl2_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=north.id,
                                        area_district_id=nl2.id)
    cav_co = OrgCompanyService().create(short_name="Excellence",
                                        region_id=south.id,
                                        area_district_id=cav.id)
    mapped = BranchService().create(code="MAP-TB", name="Mapped Branch",
                                    company_id=nl2_co.id)
    _db.session.commit()

    role = Role(name="TB Role")
    role.permissions = Permission.query.all()
    user = User(username="tbuser", email="tbuser@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"nl2_co": nl2_co, "cav_co": cav_co, "mapped": mapped}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "tbuser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _post(client, path, payload, token):
    r = client.post(path, json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Tire ──────────────────────────────────────────────────────────────

def test_a_tire_is_created_with_a_company_and_no_branch(db, client, env):
    token = _token(client)
    status, body = _post(client, "/api/v1/tires", {
        "serial_number": "T-TB1", "brand": "Bridgestone", "size": "185/65R15",
        "tire_type": "RADIAL", "company_id": env["nl2_co"].id}, token)
    assert status == 201, body
    assert body["company_id"] == env["nl2_co"].id


def test_tire_company_id_is_in_the_response_so_a_list_column_can_show_it(
        db, client, env):
    token = _token(client)
    _s, created = _post(client, "/api/v1/tires", {
        "serial_number": "T-TB2", "brand": "Bridgestone", "size": "185/65R15",
        "tire_type": "RADIAL", "company_id": env["nl2_co"].id}, token)
    status, body = _get(client, "/api/v1/tires", token)
    assert status == 200
    row = next(t for t in body["items"] if t["id"] == created["id"])
    assert row["company_id"] == env["nl2_co"].id
    assert row["company"] == "Excellence"


def test_a_string_tire_company_id_from_the_browser_form_is_coerced(
        db, client, env):
    token = _token(client)
    status, body = _post(client, "/api/v1/tires", {
        "serial_number": "T-TB3", "brand": "Bridgestone", "size": "185/65R15",
        "tire_type": "RADIAL",
        "company_id": str(env["nl2_co"].id)}, token)
    assert status == 201
    assert body["company_id"] == env["nl2_co"].id


def test_tire_creation_without_a_company_is_refused_with_a_field_message(
        db, client, env):
    token = _token(client)
    status, body = _post(client, "/api/v1/tires", {
        "serial_number": "T-TB4", "brand": "Bridgestone", "size": "185/65R15",
        "tire_type": "RADIAL"}, token)
    assert status == 400
    assert body["fields"]["company_id"] == "Company is required."


def test_the_tire_list_filters_by_company(db, client, env):
    token = _token(client)
    _post(client, "/api/v1/tires", {
        "serial_number": "T-TB5", "brand": "Bridgestone", "size": "185/65R15",
        "tire_type": "RADIAL", "company_id": env["nl2_co"].id}, token)
    _post(client, "/api/v1/tires", {
        "serial_number": "T-TB6", "brand": "Bridgestone", "size": "185/65R15",
        "tire_type": "RADIAL", "company_id": env["cav_co"].id}, token)
    _s, everything = _get(client, "/api/v1/tires", token)
    _s, narrowed = _get(
        client, f"/api/v1/tires?company_id={env['nl2_co'].id}", token)
    assert len(everything["items"]) == 2
    assert len(narrowed["items"]) == 1
    assert narrowed["items"][0]["serial_number"] == "T-TB5"


def test_a_tire_under_a_mapped_branch_inherits_its_company(db, client, env):
    token = _token(client)
    _post(client, "/api/v1/tires", {
        "serial_number": "T-TB7", "brand": "Bridgestone", "size": "185/65R15",
        "tire_type": "RADIAL", "branch_id": env["mapped"].id,
        "company_id": env["nl2_co"].id}, token)
    _s, narrowed = _get(
        client, f"/api/v1/tires?company_id={env['nl2_co'].id}", token)
    assert "T-TB7" in {t["serial_number"] for t in narrowed["items"]}


# ── Battery ───────────────────────────────────────────────────────────

def test_a_battery_is_created_with_a_company_and_no_branch(db, client, env):
    token = _token(client)
    status, body = _post(client, "/api/v1/batteries", {
        "serial_number": "B-TB1", "brand": "Motolite",
        "company_id": env["nl2_co"].id}, token)
    assert status == 201, body
    assert body["company_id"] == env["nl2_co"].id


def test_battery_company_id_is_in_the_response_so_a_list_column_can_show_it(
        db, client, env):
    token = _token(client)
    _s, created = _post(client, "/api/v1/batteries", {
        "serial_number": "B-TB2", "brand": "Motolite",
        "company_id": env["nl2_co"].id}, token)
    status, body = _get(client, "/api/v1/batteries", token)
    assert status == 200
    row = next(b for b in body["items"] if b["id"] == created["id"])
    assert row["company_id"] == env["nl2_co"].id
    assert row["company"] == "Excellence"


def test_battery_creation_without_a_company_is_refused_with_a_field_message(
        db, client, env):
    token = _token(client)
    status, body = _post(client, "/api/v1/batteries", {
        "serial_number": "B-TB3", "brand": "Motolite"}, token)
    assert status == 400
    assert body["fields"]["company_id"] == "Company is required."


def test_the_battery_list_filters_by_company(db, client, env):
    token = _token(client)
    _post(client, "/api/v1/batteries", {
        "serial_number": "B-TB4", "brand": "Motolite",
        "company_id": env["nl2_co"].id}, token)
    _post(client, "/api/v1/batteries", {
        "serial_number": "B-TB5", "brand": "Motolite",
        "company_id": env["cav_co"].id}, token)
    _s, everything = _get(client, "/api/v1/batteries", token)
    _s, narrowed = _get(
        client, f"/api/v1/batteries?company_id={env['nl2_co'].id}", token)
    assert len(everything["items"]) == 2
    assert len(narrowed["items"]) == 1
    assert narrowed["items"][0]["serial_number"] == "B-TB4"
