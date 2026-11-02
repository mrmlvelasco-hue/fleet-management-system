"""A picture attached to a Lookup row (2026-11-02).

Michael: "for the picture in the checklist we need to map the picture
base on the checklist in the vehicle body type it's possible to
assigned a picture in vehicle body type and this picture view in the
checklist and depend to the body type of selected vehicle ... the
picture is in the xlsx has a every sheet with different picture
please check and apply to the handover checklist."

Vehicle Body Type is not its own table -- Vehicle.vehicle_body_type is
a free-text column that references a row in the generic `Lookup`
table (lookup_type="VEHICLE_BODY_TYPE"). So "assign a picture to a
vehicle body type" means "assign a picture to a Lookup row", and the
system already has a proven pattern for exactly that --
CompanyProfile.logo_attachment_id via AttachmentService (see
test_company_logo.py). This reuses it rather than inventing a second
image-storage mechanism, generically on Lookup so it is available to
any lookup type, not hard-coded to VEHICLE_BODY_TYPE -- "prefer
configurable solutions... reuse generic components" per the project's
own architectural principle.

See test_handover_checklist_body_type_diagram.py for the other half:
the vehicle print payload resolving a vehicle's body type to its
Lookup row's picture.
"""
import io
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.system_admin.models import Lookup
from app.modules.user_management.models import Permission, Role, User

PNG = b"\x89PNG\r\n\x1a\n sedan diagram bytes"


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def lookup_row(app):
    row = Lookup(lookup_type="VEHICLE_BODY_TYPE", code="SEDAN",
                description="Sedan", sort_order=1)
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture()
def admin(app):
    return _user("ladmin", ["lookup.view", "lookup.update"])


@pytest.fixture()
def viewer(app):
    # No lookup.* at all -- an ordinary user who should still be able
    # to render the diagram on a checklist they're allowed to print.
    return _user("juan", ["vehicle.view"])


def _hdr(client, username="ladmin"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _upload(client, lid, name="sedan.png", data=PNG, username="ladmin"):
    return client.post(f"/api/v1/admin/lookups/{lid}/picture",
                       headers=_hdr(client, username),
                       data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def test_lookup_model_has_picture_columns(app):
    cols = {c.name for c in Lookup.__table__.columns}
    assert "picture_attachment_id" in cols
    assert "picture_filename" in cols


def test_upload_sets_the_picture(app, client, lookup_row, admin):
    r = _upload(client, lookup_row.id)
    assert r.status_code == 201
    row = db.session.get(Lookup, lookup_row.id)
    assert row.picture_attachment_id is not None
    assert row.picture_filename == "sedan.png"


def test_uploading_requires_lookup_update(app, client, lookup_row, viewer):
    r = _upload(client, lookup_row.id, username="juan")
    assert r.status_code == 403


def test_a_non_image_is_refused(app, client, lookup_row, admin):
    r = _upload(client, lookup_row.id, name="notes.txt", data=b"hello")
    assert r.status_code == 400


def test_the_picture_serves_back_exactly(app, client, lookup_row, admin):
    _upload(client, lookup_row.id)
    r = client.get(f"/api/v1/lookups/{lookup_row.id}/picture",
                  headers=_hdr(client))
    assert r.status_code == 200
    assert r.get_data() == PNG


def test_anyone_signed_in_can_view_the_picture(app, client, lookup_row,
                                               admin, viewer):
    """No lookup.view required -- a fleet user printing a handover
    checklist must see the body-type diagram without holding a System
    Administration permission."""
    _upload(client, lookup_row.id)
    r = client.get(f"/api/v1/lookups/{lookup_row.id}/picture",
                   headers=_hdr(client, "juan"))
    assert r.status_code == 200


def test_serving_with_no_picture_configured_is_a_404(app, client,
                                                      lookup_row, admin):
    r = client.get(f"/api/v1/lookups/{lookup_row.id}/picture",
                   headers=_hdr(client))
    assert r.status_code == 404


def test_the_picture_can_be_replaced(app, client, lookup_row, admin):
    _upload(client, lookup_row.id)
    second = b"\x89PNG\r\n\x1a\n second sedan"
    _upload(client, lookup_row.id, name="new.png", data=second)
    r = client.get(f"/api/v1/lookups/{lookup_row.id}/picture",
                   headers=_hdr(client))
    assert r.get_data() == second


def test_the_picture_can_be_cleared(app, client, lookup_row, admin):
    _upload(client, lookup_row.id)
    r = client.delete(f"/api/v1/admin/lookups/{lookup_row.id}/picture",
                      headers=_hdr(client))
    assert r.status_code == 200
    row = db.session.get(Lookup, lookup_row.id)
    assert row.picture_attachment_id is None
    assert row.picture_filename is None
    assert client.get(f"/api/v1/lookups/{lookup_row.id}/picture",
                      headers=_hdr(client)).status_code == 404


def test_the_lookups_list_reports_the_picture(app, client, lookup_row, admin):
    """So the admin Lookups screen can show a thumbnail without a
    second request per row, and can tell 'no picture yet' from
    'failed to load'."""
    before = json.loads(client.get("/api/v1/admin/lookups",
                                   headers=_hdr(client)).get_data(as_text=True))
    row_before = next(r for r in before["items"] if r["id"] == lookup_row.id)
    assert row_before["picture_url"] is None
    assert row_before["picture_filename"] is None

    _upload(client, lookup_row.id, name="sedan.png")

    after = json.loads(client.get("/api/v1/admin/lookups",
                                  headers=_hdr(client)).get_data(as_text=True))
    row_after = next(r for r in after["items"] if r["id"] == lookup_row.id)
    refreshed = db.session.get(Lookup, lookup_row.id)
    assert row_after["picture_url"] == (
        f"/api/v1/lookups/{lookup_row.id}/picture"
        f"?v={refreshed.picture_attachment_id}")
    assert row_after["picture_filename"] == "sedan.png"


def test_saving_description_does_not_clear_the_picture(app, client,
                                                        lookup_row, admin):
    """The picture is uploaded through its own endpoint, so an ordinary
    PUT that edits description/sort_order must leave it alone."""
    _upload(client, lookup_row.id)
    client.put(f"/api/v1/admin/lookups/{lookup_row.id}", headers=_hdr(client),
              json={"description": "Sedan (4-door)"})
    row = db.session.get(Lookup, lookup_row.id)
    assert row.picture_attachment_id is not None
