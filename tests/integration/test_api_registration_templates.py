"""Registration Templates admin API -- checklist_items on GET.

Create/update already accepted `items`, but the GET responses
(list and single) never included the checklist at all -- so even a
perfect edit-form UI would have nothing to populate the "Renewal
Checklist" section with when opening an existing template. The
create/update round-trip worked; reading it back never did.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db
from app.modules.registration_config.service import RegistrationTemplateService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    role = Role(name="RegTemplate Admin")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "registrationtemplate.view", "registrationtemplate.create",
        "registrationtemplate.update"])).all()
    user = User(username="regtmpladmin", email="rta@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return {"user": user}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "regtmpladmin", "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


class TestChecklistItemsOnGet:
    def test_get_by_id_includes_the_checklist(self, db, client, env):
        t = RegistrationTemplateService().create(
            interval_years=3,
            items=[
                {"activity_code": "OR-CR", "activity_description": "Renew OR/CR",
                 "sort_order": 1},
                {"activity_code": "EMISSION", "activity_description": "Emission Test",
                 "sort_order": 2},
            ])
        status, body = _get(
            client, f"/api/v1/registration-templates/{t.id}", _token(client))
        assert status == 200
        assert len(body["checklist_items"]) == 2
        assert body["checklist_items"][0]["activity_code"] == "OR-CR"
        assert body["checklist_items"][1]["activity_code"] == "EMISSION"

    def test_list_also_includes_the_checklist_for_each_row(self, db, client, env):
        RegistrationTemplateService().create(
            interval_years=1,
            items=[{"activity_code": "OR-CR", "activity_description": "Renew OR/CR",
                   "sort_order": 1}])
        status, body = _get(
            client, "/api/v1/registration-templates", _token(client))
        assert status == 200
        assert any(len(row.get("checklist_items", [])) == 1
                  for row in body["items"])

    def test_a_template_with_no_checklist_returns_an_empty_list(self, db, client, env):
        t = RegistrationTemplateService().create(interval_years=1)
        status, body = _get(
            client, f"/api/v1/registration-templates/{t.id}", _token(client))
        assert status == 200
        assert body["checklist_items"] == []
