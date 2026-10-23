"""Approval path list payload carries its routing steps (2026-10-23).

The React Approval Configuration screen draws each rule's routing
sequence (Draft -> Supervisor -> CFO -> Complete) next to every matrix
row. The matrix list gives an approval_path_id; the path list gave only
a level_count, so the client would have had to fetch one path detail per
row -- an N+1 of HTTP round trips to render one table.

The list now carries `level_labels`, the ordered approver labels. The
labels are NOT a second definition of how an approver is described: the
engine's own label is extracted to a module-level approver_label() that
both the engine and this payload call, so the wording on the dashboard,
in the approval chain, and on this screen cannot drift apart.
"""
import json

import pytest

from app.core.approval.engine import ApprovalEngine, approver_label
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.approval_config.service import ApprovalPathService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    admin_role = Role(name="Approval Config Admin")
    admin_role.permissions = Permission.query.filter(Permission.code.in_([
        "approvalpath.view", "approvalmatrix.view"])).all()
    admin = User(username="cfgadmin", email="cfg@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    admin.roles = [admin_role]

    supervisor = Role(name="Fleet Supervisor")
    manager = Role(name="Fleet Manager")
    approver = User(username="jdoe", email="j@e.com", first_name="Jane",
                    last_name="Doe", password_hash=hash_password("secret123"),
                    is_active=True)
    _db.session.add_all([admin_role, admin, supervisor, manager, approver])
    _db.session.commit()

    ApprovalPathService().create(name="Up-To-CFO", description="CFO Level",
                                 levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": supervisor.id},
        {"level_number": 2, "approver_type": "ROLE", "role_id": manager.id},
        {"level_number": 3, "approver_type": "USER", "user_id": approver.id},
    ])
    return admin


def _paths(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "cfgadmin", "password": "secret123"})
    token = json.loads(r.get_data(as_text=True))["access_token"]
    r = client.get("/api/v1/admin/approval-paths",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return json.loads(r.get_data(as_text=True))["items"]


def test_list_carries_the_routing_steps_in_order(db, client, env):
    row = next(p for p in _paths(client) if p["name"] == "Up-To-CFO")
    assert row["level_labels"] == [
        "Role: Fleet Supervisor", "Role: Fleet Manager", "User: Jane Doe"]
    # The count the screen already showed stays, and agrees with the steps.
    assert row["level_count"] == len(row["level_labels"]) == 3


def test_labels_come_from_the_engines_own_definition(db, client, env):
    """One definition of an approver's label, not two."""
    from app.modules.approval_config.models import ApprovalPath
    path = ApprovalPath.query.filter_by(name="Up-To-CFO").first()
    engine = ApprovalEngine()
    for level in path.levels:
        assert engine._approver_label(level) == approver_label(level)

    row = next(p for p in _paths(client) if p["name"] == "Up-To-CFO")
    assert row["level_labels"] == [approver_label(l) for l in path.levels]


def test_a_path_with_no_levels_lists_no_steps(db, client, env):
    """An empty path cannot be created through the service, but one can
    exist from an older import -- the payload must not blow up on it."""
    from app.modules.approval_config.models import ApprovalPath
    _db.session.add(ApprovalPath(name="Imported, no levels"))
    _db.session.commit()
    row = next(p for p in _paths(client) if p["name"] == "Imported, no levels")
    assert row["level_labels"] == []
    assert row["level_count"] == 0
