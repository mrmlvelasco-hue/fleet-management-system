"""Linking a System Account to an assignee, through the JSON API.

Michael: "we have a issue in saving in react for the assignee for the
field of System Account but the flask is working properly."

Exactly right, and the cause is a gap rather than a bug in either side.
`user_id` is DELIBERATELY excluded from the driver API's _WRITABLE
allow-list -- the link has rules (the account must exist and be active;
one account links to at most one assignee) and those live in
DriverService.link_user. Letting user_id through the generic setattr
path would be a second door onto the same column that bypasses every
one of them, which is precisely the reasoning recorded in drivers.py.

Flask's Jinja route honours that by calling link_user as a SEPARATE
step after the generic field update (master_data/routes.py, both create
and update). The JSON API never got that step -- so React posts
`user_id`, the allow-list silently drops it, and the field appears to
save while changing nothing. Silent, not an error, which is why it
looked like a React bug.

These tests pin the API to the same behaviour as Flask's route,
including the rules link_user enforces, so the two doors cannot drift.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.org.models import Branch
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def rig(db, app):
    sync_permissions()
    db.session.commit()
    branch = Branch(name="HQ", code="HQ")
    db.session.add(branch)
    db.session.flush()

    role = Role(name="Driver Admin")
    role.permissions = Permission.query.filter(
        Permission.code.like("driver.%")).all()
    officer = User(username="officer", email="o@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    officer.roles = [role]

    # Candidate accounts to link.
    linkable = User(username="jdelacruz", email="j@e.com",
                   password_hash=hash_password("secret123"), is_active=True)
    other = User(username="msantos", email="m@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    inactive = User(username="retired", email="r@e.com",
                   password_hash=hash_password("secret123"), is_active=False)
    db.session.add_all([officer, linkable, other, inactive])
    db.session.flush()

    driver = Driver(first_name="Juan", last_name="dela Cruz",
                   employee_number="EMP-0001", branch_id=branch.id,
                   assignee_type="DRIVER", is_active=True)
    db.session.add(driver)
    db.session.commit()
    return {"driver": driver, "linkable": linkable, "other": other,
            "inactive": inactive, "branch": branch}


def _headers(client):
    tok = client.post("/api/v1/auth/token",
                      json={"username": "officer", "password": "secret123"})
    return {"Authorization":
            f"Bearer {json.loads(tok.get_data(as_text=True))['access_token']}"}


class TestLinkingOnUpdate:
    def test_setting_user_id_actually_links_the_account(
            self, client, app, rig, db):
        """The reported bug: this silently did nothing."""
        headers = _headers(client)
        resp = client.put(f"/api/v1/drivers/{rig['driver'].id}",
                          json={"user_id": rig["linkable"].id},
                          headers=headers)
        assert resp.status_code == 200
        db.session.expire_all()
        assert Driver.query.get(rig["driver"].id).user_id == rig["linkable"].id

    def test_the_response_reports_the_new_link(self, client, app, rig):
        """A save that returns the OLD value would send the form
        straight back to showing the link as unchanged -- the same
        silent failure, one layer up."""
        headers = _headers(client)
        resp = client.put(f"/api/v1/drivers/{rig['driver'].id}",
                          json={"user_id": rig["linkable"].id},
                          headers=headers)
        body = json.loads(resp.get_data(as_text=True))
        assert body["user_id"] == rig["linkable"].id
        assert body["username"] == "jdelacruz"

    def test_clearing_the_link_unlinks(self, client, app, rig, db):
        """Clearing must work too. The React form sends "" for a
        cleared picker, and link_user treats "" as a clear."""
        headers = _headers(client)
        client.put(f"/api/v1/drivers/{rig['driver'].id}",
                   json={"user_id": rig["linkable"].id}, headers=headers)
        resp = client.put(f"/api/v1/drivers/{rig['driver'].id}",
                          json={"user_id": ""}, headers=headers)
        assert resp.status_code == 200
        db.session.expire_all()
        assert Driver.query.get(rig["driver"].id).user_id is None

    def test_clearing_does_not_deactivate_the_account(
            self, client, app, rig, db):
        """Being an assignee and having a login are separate facts --
        link_user's own docstring says so. Unlinking must leave the
        person able to sign in exactly as before."""
        headers = _headers(client)
        client.put(f"/api/v1/drivers/{rig['driver'].id}",
                   json={"user_id": rig["linkable"].id}, headers=headers)
        client.put(f"/api/v1/drivers/{rig['driver'].id}",
                   json={"user_id": ""}, headers=headers)
        db.session.expire_all()
        assert User.query.get(rig["linkable"].id).is_active is True

    def test_omitting_user_id_leaves_an_existing_link_alone(
            self, client, app, rig, db):
        """The PUT handler's own contract: it must not blank fields it
        was not given. Editing an unrelated field on a linked assignee
        must not silently unlink them."""
        headers = _headers(client)
        client.put(f"/api/v1/drivers/{rig['driver'].id}",
                   json={"user_id": rig["linkable"].id}, headers=headers)
        client.put(f"/api/v1/drivers/{rig['driver'].id}",
                   json={"nickname": "Johnny"}, headers=headers)
        db.session.expire_all()
        assert Driver.query.get(rig["driver"].id).user_id == rig["linkable"].id

    def test_relinking_the_same_account_is_a_no_op_not_an_error(
            self, client, app, rig):
        """Saving the form twice without touching the picker must not
        be rejected -- link_user handles this explicitly, and the API
        must not lose that."""
        headers = _headers(client)
        client.put(f"/api/v1/drivers/{rig['driver'].id}",
                   json={"user_id": rig["linkable"].id}, headers=headers)
        resp = client.put(f"/api/v1/drivers/{rig['driver'].id}",
                          json={"user_id": rig["linkable"].id},
                          headers=headers)
        assert resp.status_code == 200


class TestLinkRulesAreEnforced:
    def test_an_inactive_account_is_rejected_with_a_readable_message(
            self, client, app, rig):
        headers = _headers(client)
        resp = client.put(f"/api/v1/drivers/{rig['driver'].id}",
                          json={"user_id": rig["inactive"].id},
                          headers=headers)
        assert resp.status_code == 400
        assert "inactive" in json.loads(
            resp.get_data(as_text=True))["message"].lower()

    def test_an_account_already_linked_elsewhere_is_rejected(
            self, client, app, rig, db):
        """One account, one assignee. Enforced in the service so the
        administrator gets a sentence rather than a 500 from the
        UNIQUE constraint at commit time."""
        second = Driver(first_name="Maria", last_name="Santos",
                       employee_number="EMP-0002",
                       branch_id=rig["branch"].id,
                       assignee_type="DRIVER", is_active=True)
        db.session.add(second)
        db.session.commit()

        headers = _headers(client)
        client.put(f"/api/v1/drivers/{rig['driver'].id}",
                   json={"user_id": rig["linkable"].id}, headers=headers)
        resp = client.put(f"/api/v1/drivers/{second.id}",
                          json={"user_id": rig["linkable"].id},
                          headers=headers)
        assert resp.status_code == 400

    def test_a_nonexistent_account_is_rejected(self, client, app, rig):
        headers = _headers(client)
        resp = client.put(f"/api/v1/drivers/{rig['driver'].id}",
                          json={"user_id": 999999}, headers=headers)
        assert resp.status_code == 400


class TestLinkingOnCreate:
    def test_a_new_assignee_can_be_created_already_linked(
            self, client, app, rig, db):
        """Flask's create route links too (routes.py line 1437), so the
        API must as well -- otherwise a new assignee silently loses the
        account chosen on the form.

        Posted as multipart with a photo because REQUIRE_ASSIGNEE_PHOTO
        defaults to strict, which is also how the real form posts. A
        JSON-only version of this test would only pass on an install
        with that parameter switched off.
        """
        import io

        headers = _headers(client)
        resp = client.post(
            "/api/v1/drivers",
            data={
                "first_name": "Pedro",
                "last_name": "Reyes",
                "employee_number": "EMP-0003",
                # CONSULTANT, not DRIVER: REQUIRE_DRIVER_LICENSE is also
                # strict by default, and licence rules are not what this
                # test is about.
                "assignee_type": "CONSULTANT",
                "branch_id": str(rig["branch"].id),
                "user_id": str(rig["other"].id),
                "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "p.png"),
            },
            headers=headers, content_type="multipart/form-data")
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = json.loads(resp.get_data(as_text=True))
        assert body["user_id"] == rig["other"].id
