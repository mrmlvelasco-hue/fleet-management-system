"""GET/PUT /api/v1/admin/print-templates -- what the editor screen reads.

The React editor shows "Active template" and "Last updated ..." in its
header, so the API has to send `is_active` and `updated_at`; without them
the header would have to guess.
"""
import json

import pytest

from app.cli import _seed_print_templates
from app.core.security.password import hash_password
from app.extensions import db
from app.modules.system_admin.models import PrintTemplate
from app.modules.user_management.models import Permission, Role, User


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


@pytest.fixture()
def editor(app):
    role = Role(name="r-tpl", description="r")
    db.session.add(role)
    db.session.flush()
    for c in ("sysparam.view", "sysparam.update"):
        role.permissions.append(_perm(c))
    u = User(username="tpleditor", email="tpleditor@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             first_name="Tpl", last_name="Editor")
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    _seed_print_templates()
    return u


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "tpleditor", "password": "secret123"})
    return {"Authorization": "Bearer "
            + json.loads(r.get_data(as_text=True))["access_token"]}


def _oath():
    return PrintTemplate.query.filter_by(code="OATH_OF_UNDERTAKING").first()


def test_detail_carries_status_and_last_updated(app, client, editor):
    r = client.get(f"/api/v1/admin/print-templates/{_oath().id}",
                   headers=_hdr(client))
    body = json.loads(r.get_data(as_text=True))
    assert r.status_code == 200
    assert body["is_active"] is True
    assert body["updated_at"]
    assert body["tokens"]


def test_list_carries_last_updated(app, client, editor):
    r = client.get("/api/v1/admin/print-templates", headers=_hdr(client))
    item = json.loads(r.get_data(as_text=True))["items"][0]
    assert "updated_at" in item and item["updated_at"]


def test_save_returns_the_new_last_updated(app, client, editor):
    r = client.put(f"/api/v1/admin/print-templates/{_oath().id}",
                   headers=_hdr(client),
                   json={"body_html": "<p>{ASSIGNEE_NAME}</p>"})
    body = json.loads(r.get_data(as_text=True))
    assert r.status_code == 200
    assert body["body_html"] == "<p>{ASSIGNEE_NAME}</p>"
    # One Z, no "+00:00Z": the value was just written and is still aware.
    assert body["updated_at"].endswith("Z")
    assert "+" not in body["updated_at"]
