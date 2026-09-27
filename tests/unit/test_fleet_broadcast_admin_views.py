"""Fleet Broadcast admin: acknowledgements and unacknowledged views.

The acknowledgements endpoint previously returned only id, user_id,
user_name, acknowledged_at -- enough to prove someone acknowledged, not
enough to tell an admin WHO in organizational terms (which the UI needs:
employee ID, role, department, branch). The data was already on User;
nothing here needed a new table, just returning fields already joined.

The unacknowledged endpoint is genuinely new: nothing previously computed
"who is in this broadcast's audience but has not acknowledged it", which
is a real question ("who do I need to follow up with") distinct from
"who has acknowledged" (a simple query against one table). It reuses
FleetBroadcastService.user_matches_broadcast_audience -- the SAME
audience rule the user's own inbox is filtered by -- rather than a
second, competing definition of "who receives this broadcast".
"""
import json

import pytest

from app.extensions import db
from app.core.security.password import hash_password
from app.modules.system_admin.models import InAppNotification
from app.modules.system_admin.services.fleet_broadcast_service import (
    FleetBroadcastService)
from app.modules.master_data.org.models import Branch, Department
from app.modules.user_management.models import Permission, Role, User
from app.core.security.registry import sync_permissions


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    branch = Branch(name="Manila", code="MNL")
    db.session.add(branch)
    db.session.flush()
    dept = Department(name="Operations", code="OPS", branch_id=branch.id)
    role = Role(name="Driver", is_system_role=False)
    db.session.add_all([dept, role])
    db.session.flush()

    admin_role = Role(name="Fleet Broadcast Admin")
    admin_role.permissions = Permission.query.filter(
        Permission.code.in_(["fleetbroadcast.view", "fleetbroadcast.update"])).all()
    db.session.add(admin_role)
    db.session.flush()

    admin = User(username="ackadmin", email="ackadmin@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    admin.roles.append(admin_role)
    acker = User(username="acker", email="acker@e.com",
                 password_hash=hash_password("secret123"), is_active=True,
                 employee_id="EMP-001", branch_id=branch.id,
                 department_id=dept.id)
    non_acker = User(username="nonacker", email="nonacker@e.com",
                     password_hash=hash_password("secret123"), is_active=True,
                     employee_id="EMP-002", branch_id=branch.id,
                     department_id=dept.id)
    acker.roles.append(role)
    non_acker.roles.append(role)
    db.session.add_all([admin, acker, non_acker])
    db.session.commit()
    return {"admin": admin, "acker": acker, "non_acker": non_acker,
            "branch": branch, "dept": dept, "role": role}


def _publish(admin, **kw):
    svc = FleetBroadcastService()
    payload = dict(
        user=admin, broadcast_no="FB-ACK-1", broadcast_type="ADVISORY",
        category="FUEL", title="Fuel advisory", message="Prices are up.",
        priority="HIGH", recipients=[{"recipient_type": "ALL_USERS"}])
    payload.update(kw)
    row = svc.create(**payload)
    svc.publish(row.id, user=admin)
    return row


def _token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _post(client, url, token):
    r = client.post(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


class TestAcknowledgementsEnrichment:
    def test_includes_employee_id_role_department_and_branch(self, client, env):
        row = _publish(env["admin"])
        FleetBroadcastService().acknowledge(row.id, user=env["acker"])

        status, body = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/acknowledgements",
            _token(client, "ackadmin"),
        )
        assert status == 200
        item = body["items"][0]
        assert item["employee_id"] == "EMP-001"
        assert item["role"] == "Driver"
        assert item["department"] == "Operations"
        assert item["branch"] == "Manila"

    def test_still_returns_the_original_fields_untouched(self, client, env):
        # The existing contract must not break -- anything already
        # reading user_id/user_name/acknowledged_at keeps working.
        row = _publish(env["admin"])
        FleetBroadcastService().acknowledge(row.id, user=env["acker"])

        _, body = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/acknowledgements",
            _token(client, "ackadmin"),
        )
        item = body["items"][0]
        assert item["user_id"] == env["acker"].id
        assert item["user_name"]
        assert item["acknowledged_at"]

    def test_a_user_with_no_role_branch_or_department_gets_nulls_not_errors(
            self, client, env):
        row = _publish(env["admin"])
        bare = User(username="bare", email="bare@e.com",
                   password_hash=hash_password("secret123"), is_active=True)
        db.session.add(bare)
        db.session.commit()
        FleetBroadcastService().acknowledge(row.id, user=bare)

        status, body = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/acknowledgements",
            _token(client, "ackadmin"),
        )
        assert status == 200
        item = body["items"][0]
        assert item["employee_id"] is None
        assert item["role"] is None
        assert item["department"] is None
        assert item["branch"] is None


class TestListCounts:
    def test_list_reports_recipient_and_acknowledged_counts(self, client, env):
        # The list page's Recipients/Acknowledged columns and progress
        # bar have nothing to render from today -- _broadcast_json only
        # carries the audience RULE (e.g. "All Users"), never how many
        # people that resolves to or how many of them have acknowledged.
        row = _publish(env["admin"])
        FleetBroadcastService().acknowledge(row.id, user=env["acker"])

        status, body = _get(
            client, "/api/v1/admin/fleet-broadcasts",
            _token(client, "ackadmin"),
        )
        assert status == 200
        item = next(i for i in body["items"] if i["id"] == row.id)
        # env has 3 active users total: admin, acker, non_acker -- all
        # match ALL_USERS.
        assert item["recipient_count"] == 3
        assert item["acknowledged_count"] == 1

    def test_a_draft_never_notified_anyone_reports_zero_counts(self, client, env):
        svc = FleetBroadcastService()
        draft = svc.create(
            user=env["admin"], broadcast_no="FB-ACK-2", broadcast_type="ADVISORY",
            category="FUEL", title="Draft", message="Not published yet.",
            priority="NORMAL", recipients=[{"recipient_type": "ALL_USERS"}],
        )

        _, body = _get(
            client, "/api/v1/admin/fleet-broadcasts",
            _token(client, "ackadmin"),
        )
        item = next(i for i in body["items"] if i["id"] == draft.id)
        assert item["recipient_count"] == 0
        assert item["acknowledged_count"] == 0


class TestArchive:
    def test_archives_a_published_broadcast(self, client, env):
        row = _publish(env["admin"])

        status, body = _post(
            client, f"/api/v1/admin/fleet-broadcasts/{row.id}/archive",
            _token(client, "ackadmin"),
        )
        assert status == 200
        assert body["status"] == "ARCHIVED"

    def test_refuses_to_archive_a_draft(self, client, env):
        svc = FleetBroadcastService()
        draft = svc.create(
            user=env["admin"], broadcast_no="FB-ARCH-1", broadcast_type="ADVISORY",
            category="FUEL", title="Draft", message="Not published yet.",
            priority="NORMAL", recipients=[{"recipient_type": "ALL_USERS"}],
        )

        status, body = _post(
            client, f"/api/v1/admin/fleet-broadcasts/{draft.id}/archive",
            _token(client, "ackadmin"),
        )
        assert status == 400
        assert draft.status == "DRAFT"

    def test_404_for_a_nonexistent_broadcast(self, client, env):
        status, _ = _post(
            client, "/api/v1/admin/fleet-broadcasts/999999/archive",
            _token(client, "ackadmin"),
        )
        assert status == 404

    def test_requires_permission(self, client, env):
        row = _publish(env["admin"])
        outsider = User(username="archoutsider", email="archoutsider@e.com",
                        password_hash=hash_password("secret123"),
                        is_active=True)
        db.session.add(outsider)
        db.session.commit()

        status, _ = _post(
            client, f"/api/v1/admin/fleet-broadcasts/{row.id}/archive",
            _token(client, "archoutsider"),
        )
        assert status == 403


class TestUnacknowledged:
    def test_lists_someone_in_the_audience_who_has_not_acknowledged(
            self, client, env):
        row = _publish(env["admin"])
        FleetBroadcastService().acknowledge(row.id, user=env["acker"])

        status, body = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/unacknowledged",
            _token(client, "ackadmin"),
        )
        assert status == 200
        ids = [i["user_id"] for i in body["items"]]
        assert env["non_acker"].id in ids
        assert env["acker"].id not in ids
        # The admin themself received it (ALL_USERS) and hasn't
        # acknowledged either -- included the same as anyone else.
        assert env["admin"].id in ids

    def test_reports_unread_when_the_notification_was_never_opened(
            self, client, env):
        row = _publish(env["admin"])

        _, body = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/unacknowledged",
            _token(client, "ackadmin"),
        )
        entry = next(i for i in body["items"] if i["user_id"] == env["non_acker"].id)
        assert entry["status"] == "UNREAD"
        assert entry["notification_sent_at"]
        assert entry["last_seen_at"] is None

    def test_reports_read_once_the_notification_was_opened(self, client, env):
        row = _publish(env["admin"])
        note = InAppNotification.query.filter_by(
            user_id=env["non_acker"].id, reference_table="fleet_broadcasts",
            reference_id=row.id,
        ).first()
        note.is_read = True
        from datetime import datetime
        note.read_at = datetime.utcnow()
        db.session.commit()

        _, body = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/unacknowledged",
            _token(client, "ackadmin"),
        )
        entry = next(i for i in body["items"] if i["user_id"] == env["non_acker"].id)
        assert entry["status"] == "READ"
        assert entry["last_seen_at"]

    def test_reports_not_acknowledged_when_no_notification_row_exists_at_all(
            self, client, env):
        # A recipient added to the audience AFTER publish (or whose
        # notification write failed) has no InAppNotification row to
        # read a status from -- distinct from "unread", which implies
        # one was actually delivered.
        row = _publish(env["admin"])
        late = User(username="late", email="late@e.com",
                   password_hash=hash_password("secret123"), is_active=True)
        db.session.add(late)
        db.session.commit()

        _, body = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/unacknowledged",
            _token(client, "ackadmin"),
        )
        entry = next(i for i in body["items"] if i["user_id"] == late.id)
        assert entry["status"] == "NOT_ACKNOWLEDGED"
        assert entry["notification_sent_at"] is None

    def test_includes_employee_id_role_department_and_branch(self, client, env):
        row = _publish(env["admin"])

        _, body = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/unacknowledged",
            _token(client, "ackadmin"),
        )
        entry = next(i for i in body["items"] if i["user_id"] == env["non_acker"].id)
        assert entry["employee_id"] == "EMP-002"
        assert entry["role"] == "Driver"
        assert entry["department"] == "Operations"
        assert entry["branch"] == "Manila"

    def test_requires_the_same_view_permission_as_acknowledgements(
            self, client, env):
        row = _publish(env["admin"])
        outsider = User(username="outsider", email="outsider@e.com",
                        password_hash=hash_password("secret123"),
                        is_active=True)
        db.session.add(outsider)
        db.session.commit()

        status, _ = _get(
            client,
            f"/api/v1/admin/fleet-broadcasts/{row.id}/unacknowledged",
            _token(client, "outsider"),
        )
        assert status == 403