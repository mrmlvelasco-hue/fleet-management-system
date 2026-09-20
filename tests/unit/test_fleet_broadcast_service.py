"""Fleet Broadcast service — audience attachment, numbering, publication.

Two defects motivated the first tests here, both invisible across separate
HTTP requests and both fatal inside one.

`_replace_recipients` built each FleetBroadcastRecipient with an explicit
`broadcast_id` and handed it to `db.session.add()`, never to the
`broadcast.recipients` collection. The `.clear()` immediately above it had
already forced that collection to load (it is `lazy="selectin"`), so it sat
in the identity map as a loaded, EMPTY list. The app runs with
`expire_on_commit=False`, so the commit did not refresh it either.

Consequences, both real:

  * `POST`/`PUT /admin/fleet-broadcasts` serialise `row.recipients` straight
    into the response, so a broadcast whose audience saved perfectly came
    back as `"recipients": []`.
  * `publish()` guards on `if not row.recipients`, so publishing a broadcast
    created in the same session raised "At least one broadcast audience is
    required" about an audience that was sitting in the database.

The fix is to append to the relationship and let SQLAlchemy populate the FK,
which is also what keeps the cascade honest.
"""
import pytest

from app.extensions import db
from app.core.security.password import hash_password
from app.modules.system_admin.services.fleet_broadcast_service import (
    FleetBroadcastService)
from app.modules.user_management.models import User


def _user(username):
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def env(app):
    return {"admin": _user("bcadmin"), "driver": _user("bcdriver")}


def _draft(admin, **kw):
    payload = dict(
        user=admin,
        broadcast_no="FB-2026-000001",
        broadcast_type="ANNOUNCEMENT",
        category="GENERAL",
        title="Typhoon advisory",
        message="Secure all units before 1600H.",
        priority="HIGH",
        recipients=[{"recipient_type": "ALL_USERS"}],
    )
    payload.update(kw)
    return FleetBroadcastService().create(**payload)


def _fb_document_type():
    """The FB document type + scheme exactly as `flask seed` lays it down."""
    from app.modules.document_config.models import (
        DocumentType, NumberingScheme)

    dt = DocumentType(code="FB", name="Fleet Broadcast",
                      requires_approval=False, auto_numbering=True,
                      printable=True, mobile_available=True,
                      attachment_allowed=False)
    db.session.add(dt)
    db.session.flush()
    db.session.add(NumberingScheme(
        document_type_id=dt.id, prefix="FB", include_year=True,
        include_month=False, digit_count=4, separator="-",
        reset_policy="YEARLY"))
    db.session.commit()
    return dt


def test_create_numbers_the_broadcast_from_the_numbering_engine(env):
    """A broadcast is a document like any other: the admin should not be
    inventing its reference by hand, and two admins typing at once should
    not be able to collide. Blank number -> the generic engine issues it."""
    _fb_document_type()

    row = _draft(env["admin"], broadcast_no=None)

    assert row.broadcast_no.startswith("FB-")
    assert row.broadcast_no.endswith("-0001")


def test_consecutive_broadcasts_take_consecutive_numbers(env):
    _fb_document_type()

    first = _draft(env["admin"], broadcast_no=None)
    second = _draft(env["admin"], broadcast_no=None, title="Second notice")

    assert first.broadcast_no != second.broadcast_no
    assert second.broadcast_no.endswith("-0002")


def test_an_explicit_broadcast_number_is_still_honoured(env):
    """Migrated or back-dated notices keep the number they arrived with."""
    _fb_document_type()

    row = _draft(env["admin"], broadcast_no="LEGACY-0007")

    assert row.broadcast_no == "LEGACY-0007"


def test_a_missing_numbering_scheme_is_a_validation_error_not_a_crash(env):
    """Without the FB scheme configured the admin gets a 400 they can act
    on, rather than a 500 from a NoSchemeError escaping the service."""
    with pytest.raises(ValueError) as exc:
        _draft(env["admin"], broadcast_no=None)

    assert "numbering" in str(exc.value).lower()


def test_create_returns_the_row_with_its_audience_attached(env):
    """The row handed back is what the API serialises, so its audience has
    to be on it — not merely in the database."""
    row = _draft(env["admin"])

    assert len(row.recipients) == 1
    assert row.recipients[0].recipient_type == "ALL_USERS"
    assert row.recipients[0].broadcast_id == row.id


def test_publish_succeeds_on_a_broadcast_created_in_the_same_session(env):
    """Publishing right after creating is the ordinary admin flow and the
    one every test of this module needs."""
    row = _draft(env["admin"])

    published = FleetBroadcastService().publish(row.id, user=env["admin"])

    assert published.status == "PUBLISHED"


def test_replacing_the_audience_leaves_exactly_the_new_rows(env):
    """An edit swaps the audience rather than accumulating it."""
    row = _draft(env["admin"])

    FleetBroadcastService().update(
        row.id,
        recipients=[{"recipient_type": "SPECIFIC_USER",
                     "user_id": env["driver"].id}],
    )

    assert len(row.recipients) == 1
    assert row.recipients[0].recipient_type == "SPECIFIC_USER"
    assert row.recipients[0].user_id == env["driver"].id
