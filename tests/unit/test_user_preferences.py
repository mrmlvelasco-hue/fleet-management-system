"""Per-user UI preferences -- e.g. the dashboard's chart choices.

Kept on the SERVER, per user, so a choice made on the laptop shows on
the phone too (the mobile app renders the same Dashboard).

  GET /api/v1/me/preferences          -> {key: value, ...} for the caller
  PUT /api/v1/me/preferences/<key>    body {"value": <any JSON>}

Only ever the caller's own preferences. Keys are short identifiers;
values are small JSON (a preference is a setting, not a document).
Personal display state is not a business record, so it is excluded from
the audit trail (one row per chart click would bury real history).
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.user_management.models import Role, User


@pytest.fixture()
def users(db):
    sync_permissions()
    for name in ("alice", "bob"):
        u = User(username=name, email=f"{name}@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
        u.roles = [Role(name=f"r-{name}")]
        db.session.add(u)
    db.session.commit()


def _hdr(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


KEY = "dashboard.costTrend.type"


def test_no_preferences_yet_is_an_empty_object(client, db, users):
    r = client.get("/api/v1/me/preferences", headers=_hdr(client, "alice"))
    assert r.status_code == 200
    assert r.get_json() == {}


def test_a_saved_preference_comes_back(client, db, users):
    h = _hdr(client, "alice")
    r = client.put(f"/api/v1/me/preferences/{KEY}", headers=h,
                   json={"value": "bar"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert client.get("/api/v1/me/preferences", headers=h).get_json() == {
        KEY: "bar"}


def test_saving_again_replaces_the_value(client, db, users):
    h = _hdr(client, "alice")
    client.put(f"/api/v1/me/preferences/{KEY}", headers=h, json={"value": "bar"})
    client.put(f"/api/v1/me/preferences/{KEY}", headers=h, json={"value": "area"})
    assert client.get("/api/v1/me/preferences", headers=h).get_json()[KEY] == "area"


def test_structured_values_round_trip(client, db, users):
    h = _hdr(client, "alice")
    val = {"groupBy": "DEPARTMENT", "branchId": 3}
    client.put("/api/v1/me/preferences/dashboard.costTrendByBranch.groupBy",
               headers=h, json={"value": val})
    got = client.get("/api/v1/me/preferences", headers=h).get_json()
    assert got["dashboard.costTrendByBranch.groupBy"] == val


def test_each_user_sees_only_their_own(client, db, users):
    client.put(f"/api/v1/me/preferences/{KEY}", headers=_hdr(client, "alice"),
               json={"value": "bar"})
    r = client.get("/api/v1/me/preferences", headers=_hdr(client, "bob"))
    assert r.get_json() == {}


@pytest.mark.parametrize("key", ["bad key", "x" * 81, "a/b", ""])
def test_malformed_keys_are_rejected(client, db, users, key):
    r = client.put(f"/api/v1/me/preferences/{key}",
                   headers=_hdr(client, "alice"), json={"value": 1})
    assert r.status_code in (400, 404, 405)


def test_oversized_values_are_rejected(client, db, users):
    r = client.put(f"/api/v1/me/preferences/{KEY}",
                   headers=_hdr(client, "alice"),
                   json={"value": "x" * 5000})
    assert r.status_code == 400


def test_a_missing_value_is_rejected(client, db, users):
    r = client.put(f"/api/v1/me/preferences/{KEY}",
                   headers=_hdr(client, "alice"), json={})
    assert r.status_code == 400


def test_preferences_need_a_signed_in_user(client, db, users):
    assert client.get("/api/v1/me/preferences").status_code == 401


def test_preference_changes_do_not_fill_the_audit_trail(client, db, users):
    from app.core.models.audit_log import AuditLog
    before = AuditLog.query.filter_by(table_name="user_preferences").count()
    h = _hdr(client, "alice")
    client.put(f"/api/v1/me/preferences/{KEY}", headers=h, json={"value": "bar"})
    client.put(f"/api/v1/me/preferences/{KEY}", headers=h, json={"value": "line"})
    assert AuditLog.query.filter_by(table_name="user_preferences").count() == before
