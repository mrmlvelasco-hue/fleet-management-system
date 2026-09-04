"""LIST_PAGES actually drives page size.

The parameter has existed since the beginning -- seeded, shown in System
Parameters, described as "Rows shown per page in list screens" -- and
NOTHING read it. Every list carried its own hard-coded default, so the
setting looked configurable and did nothing, and the client's page
length did not match what the parameter said.

That is a direct breach of "no values shall be hardcoded" in the master
prompt.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.api.pagination import (FALLBACK_PAGE_SIZE, MAX_PAGE_SIZE,
                                        default_page_size, resolve_page_size)
from app.modules.system_admin.models import SystemParameter
from app.modules.user_management.models import Permission, Role, User


def _set(value, data_type="INTEGER"):
    p = SystemParameter.query.filter_by(code="LIST_PAGES").first()
    if p is None:
        p = SystemParameter(code="LIST_PAGES", value=str(value),
                            data_type=data_type, group_name="UI_PREFERENCES",
                            description="Rows per page")
        db.session.add(p)
    else:
        p.value = str(value)
    db.session.commit()


def test_the_parameter_is_read(app):
    _set(40)
    assert default_page_size() == 40


def test_a_missing_parameter_falls_back(app):
    """A database without the row must behave like one with the seeded
    value, not crash a list screen."""
    assert default_page_size() == FALLBACK_PAGE_SIZE


def test_a_nonsense_value_does_not_break_the_list(app):
    """A misconfigured parameter must never take a list down. The page
    still renders and an administrator can fix the value."""
    _set("banana", data_type="STRING")
    assert default_page_size() == FALLBACK_PAGE_SIZE


def test_zero_or_negative_falls_back(app):
    _set(0)
    assert default_page_size() == FALLBACK_PAGE_SIZE


def test_an_absurd_value_is_capped(app):
    """Someone setting 10000 should get a large page, not a request that
    loads a quarter of a multi-million-row table and times out."""
    _set(10000)
    assert default_page_size() == MAX_PAGE_SIZE


def test_an_explicit_per_page_still_wins(app):
    """An export or a mobile screen may legitimately want a different
    size. The parameter sets the DEFAULT, not a straitjacket."""
    _set(40)
    assert resolve_page_size(10) == 10


def test_an_explicit_per_page_is_still_capped(app):
    _set(40)
    assert resolve_page_size(99999, maximum=100) == 100


def test_blank_or_bad_input_uses_the_parameter(app):
    _set(40)
    for bad in (None, "", "abc", 0, -5):
        assert resolve_page_size(bad) == 40


def test_the_checklist_list_honours_it(app, client):
    """End to end: the parameter changes what the endpoint returns."""
    role = Role(name="R", description="r")
    db.session.add(role)
    db.session.flush()
    for code in ("checklist.view", "checklist.review"):
        m, a = code.split(".")
        perm = Permission.query.filter_by(code=code).first()
        if perm is None:
            perm = Permission(code=code, module=m, action=a)
            db.session.add(perm)
            db.session.flush()
        role.permissions.append(perm)
    u = User(username="u", email="u@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    _set(7)

    tok = json.loads(client.post("/api/v1/auth/token",
        json={"username": "u", "password": "secret123"}
    ).get_data(as_text=True))["access_token"]
    body = json.loads(client.get("/api/v1/checklists",
        headers={"Authorization": f"Bearer {tok}"}).get_data(as_text=True))

    assert body["per_page"] == 7


def test_me_reports_it_so_react_agrees_with_the_api(app, client):
    """Carried on /me because it is already fetched once per session --
    a settings call per list screen would be a round trip to learn a
    number that never changes."""
    u = User(username="u2", email="u2@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    db.session.add(u)
    db.session.commit()
    _set(33)

    tok = json.loads(client.post("/api/v1/auth/token",
        json={"username": "u2", "password": "secret123"}
    ).get_data(as_text=True))["access_token"]
    body = json.loads(client.get("/api/v1/me",
        headers={"Authorization": f"Bearer {tok}"}).get_data(as_text=True))

    assert body["list_page_size"] == 33
