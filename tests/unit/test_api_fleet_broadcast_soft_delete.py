"""The inbox must respect the soft-delete flag.

`list_my_fleet_broadcasts` filtered on `status == "PUBLISHED"` and the date
window only. It never checked `is_active`, so a soft-deleted broadcast --
the convention this whole codebase uses instead of a hard delete, per
BaseModel -- still reached every recipient in its audience. Deleting a
broadcast from the admin screen did nothing for the people it was already
notifying.
"""
import json

import pytest

from app.extensions import db
from app.core.security.password import hash_password
from app.modules.system_admin.services.fleet_broadcast_service import (
    FleetBroadcastService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(app):
    admin = User(username="isadm", email="isadm@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    driver = User(username="isdrv", email="isdrv@e.com",
                  password_hash=hash_password("secret123"), is_active=True,
                  mobile_access=True)
    db.session.add_all([admin, driver])
    db.session.commit()
    return {"admin": admin, "driver": driver}


def _inbox(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "isdrv", "password": "secret123"})
    token = json.loads(r.get_data(as_text=True)).get("access_token")
    r = client.get("/api/v1/my/fleet-broadcasts",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return json.loads(r.get_data(as_text=True))["items"]


def test_a_soft_deleted_broadcast_no_longer_reaches_the_inbox(client, env):
    svc = FleetBroadcastService()
    row = svc.create(
        user=env["admin"], broadcast_no="FB-2026-0020",
        broadcast_type="ANNOUNCEMENT", category="GENERAL",
        title="Withdrawn notice", message="Superseded.", priority="NORMAL",
        recipients=[{"recipient_type": "ALL_USERS"}])
    svc.publish(row.id, user=env["admin"])
    assert [b["broadcast_no"] for b in _inbox(client)] == ["FB-2026-0020"]

    row.is_active = False
    db.session.commit()

    assert _inbox(client) == []
