"""Evidence photos on ATTENTION and FAILED responses.

Client requirement: a driver photographs what they found so the Fleet
Officer reviewing the report can see it.

The photo lives on the LINE, not the defect. A response is what the
driver records; a defect is the observation that follows. Hanging the
picture off the defect would mean it can only exist once someone has
typed a description -- backwards on a phone, where you photograph the
cracked light and then write about it.
"""
import io
import json

import pytest
from werkzeug.datastructures import FileStorage

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.assignment_service import (
    VehicleAssignmentService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.vehicle_checklist.models import (
    VehicleChecklistLine)
from app.modules.transactions.vehicle_checklist.service import (
    VehicleChecklistService)
from app.modules.user_management.models import Permission, Role, User

JPEG = b"\xff\xd8\xff\xe0 fake jpeg bytes"
DRIVER = ["vehicle.view", "checklist.view", "checklist.create",
          "checklist.update", "checklist.submit"]


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes, branch_id):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=branch_id, mobile_access=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()
    ju = _user("juan", DRIVER, b.id)
    _user("maria", DRIVER, b.id)
    juan = Driver(person_id="P1", employee_number="E1", first_name="Juan",
                  last_name="Cruz", assignee_type="DRIVER", branch_id=b.id)
    db.session.add(juan)
    db.session.commit()
    DriverService().link_user(juan.id, ju.id)
    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Vios", year=2009, branch_id=b.id,
                                conduction_number="A1", plate_number="MINE-1")
    db.session.commit()
    VehicleAssignmentService().assign(v.id, juan.id, source="MANUAL")

    svc = VehicleChecklistService()
    tpl = svc.create_template(name="Daily")
    cat = svc.add_category(tpl.id, name="Lights", sort_order=1)
    # photo_required_on_fail set explicitly -- it does NOT follow from
    # is_safety. The BLOWBAGETS seed sets both on its safety categories,
    # and the payload surfaces this so the app can warn a driver a
    # picture is expected before they try to submit.
    svc.add_item(cat.id, name="Headlights", is_safety=True,
                 photo_required_on_fail=True)
    db.session.commit()
    cl = svc.create(vehicle_id=v.id, template_id=tpl.id, user=ju)
    db.session.commit()
    return cl, ju


def _hdr(client, username="juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _line(cl):
    return VehicleChecklistLine.query.filter_by(checklist_id=cl.id).first()


def _respond(cl, line, response):
    line.response = response
    db.session.commit()


def _post_photo(client, cl, line, username="juan", data=JPEG):
    return client.post(
        f"/api/v1/checklists/{cl.id}/lines/{line.id}/photo",
        headers=_hdr(client, username),
        data={"file": (io.BytesIO(data), "defect.jpg")},
        content_type="multipart/form-data")


def test_a_photo_can_be_attached_to_a_FAILED_item(app, client, env):
    cl, _ju = env
    line = _line(cl)
    _respond(cl, line, "FAILED")

    r = _post_photo(client, cl, line)

    assert r.status_code == 201
    assert db.session.get(VehicleChecklistLine,
                          line.id).photo_attachment_id is not None


def test_a_photo_can_be_attached_to_an_ATTENTION_item(app, client, env):
    cl, _ju = env
    line = _line(cl)
    _respond(cl, line, "ATTENTION")

    assert _post_photo(client, cl, line).status_code == 201


def test_a_PASSED_item_is_refused(app, client, env):
    """A photo of something that passed is not evidence of anything, and
    allowing it would fill the store with pictures nobody looks at -- 45
    items, every day."""
    cl, _ju = env
    line = _line(cl)
    _respond(cl, line, "PASS")

    r = _post_photo(client, cl, line)

    assert r.status_code == 400
    assert "Attention or Failed" in json.loads(
        r.get_data(as_text=True))["message"]


def test_an_unanswered_item_is_refused(app, client, env):
    cl, _ju = env
    assert _post_photo(client, cl, _line(cl)).status_code == 400


def test_the_photo_appears_on_the_payload(app, client, env):
    """So the app can show a thumbnail without a second lookup."""
    cl, _ju = env
    line = _line(cl)
    _respond(cl, line, "FAILED")
    _post_photo(client, cl, line)

    body = json.loads(client.get(f"/api/v1/checklists/{cl.id}",
                                 headers=_hdr(client)).get_data(as_text=True))
    item = body["groups"][0]["items"][0]

    assert item["photo_attachment_id"] is not None
    assert item["photo_required_on_fail"] is True


def test_a_photo_can_be_retaken(app, client, env):
    cl, _ju = env
    line = _line(cl)
    _respond(cl, line, "FAILED")
    _post_photo(client, cl, line)
    first = db.session.get(VehicleChecklistLine, line.id).photo_attachment_id

    _post_photo(client, cl, line, data=b"\xff\xd8\xff\xe0 second shot")

    assert db.session.get(
        VehicleChecklistLine, line.id).photo_attachment_id != first


def test_a_photo_can_be_removed_from_a_draft(app, client, env):
    cl, _ju = env
    line = _line(cl)
    _respond(cl, line, "FAILED")
    _post_photo(client, cl, line)

    r = client.delete(f"/api/v1/checklists/{cl.id}/lines/{line.id}/photo",
                      headers=_hdr(client))

    assert r.status_code == 200
    assert db.session.get(VehicleChecklistLine,
                          line.id).photo_attachment_id is None


def test_another_driver_cannot_attach_to_my_inspection(app, client, env):
    """404, not 403 -- ids must not be probable, same as everywhere else
    in this module."""
    cl, _ju = env
    line = _line(cl)
    _respond(cl, line, "FAILED")

    assert _post_photo(client, cl, line, username="maria").status_code == 404


def test_a_submitted_inspection_refuses_new_photos(app, client, env):
    """Evidence that can be swapped after review is not evidence."""
    cl, ju = env
    line = _line(cl)
    _respond(cl, line, "FAILED")
    from app.modules.transactions.vehicle_checklist.models import (
        ChecklistDefect)
    db.session.add(ChecklistDefect(checklist_id=cl.id, line_id=line.id,
                                   item_name=line.item_name,
                                   observation="Cracked lens"))
    db.session.commit()
    VehicleChecklistService().submit(cl.id, user=ju)
    db.session.commit()

    r = _post_photo(client, cl, line)

    assert r.status_code == 409


def test_attaching_right_after_marking_ATTENTION_in_the_app_is_refused(
        app, client, env):
    """Reported from UAT 2026-10-20: a driver taps ATTENTION, the button
    highlights, they immediately tap the camera -- and get refused with
    exactly the "Mark the item first" message, even though the item
    LOOKS marked.

    The mobile screen only holds the response in local state; it is
    not sent to the server until Save Draft / Submit (PUT
    /checklists/<id>). The photo endpoint checks the LINE'S OWN
    response, which is whatever was last saved. Reproduced here by NOT
    calling _respond (which writes straight to the DB, bypassing the
    app entirely) -- the line is exactly as fresh as a server that has
    never heard about the tap.
    """
    cl, _ju = env
    line = _line(cl)

    r = _post_photo(client, cl, line)

    assert r.status_code == 400
    assert "Attention or Failed" in json.loads(
        r.get_data(as_text=True))["message"]


def test_saving_the_response_first_then_attaching_succeeds(app, client, env):
    """The fix's contract from the client's side: send the response to
    the server (PUT /checklists/<id>, exactly what Save Draft already
    does) BEFORE the photo POST, and the same request that used to 400
    now succeeds -- no server change needed for this half of the bug."""
    cl, _ju = env
    line = _line(cl)

    save = client.put(
        f"/api/v1/checklists/{cl.id}",
        headers=_hdr(client),
        json={"responses": [{"line_id": line.id, "response": "ATTENTION"}]})
    assert save.status_code == 200

    assert _post_photo(client, cl, line).status_code == 201


def test_the_detail_payload_carries_the_real_letterhead(app, client, env):
    """The printed checklist said "Fleet Management System" -- a
    hardcoded product name standing in for the client's own company."""
    cl, _ju = env
    body = json.loads(client.get(f"/api/v1/checklists/{cl.id}",
                                 headers=_hdr(client)).get_data(as_text=True))
    assert "company" in body
    assert "company_name" in body["company"]
    assert "logo_url" in body["company"]


def test_the_photo_can_actually_be_downloaded_by_its_own_driver(
        app, client, env):
    """Reported from UAT 2026-10-20 alongside the ATTENTION-timing bug:
    even a photo that WAS successfully attached could never be viewed
    or downloaded afterwards -- /attachments/<id>/download 404'd for
    every checklist photo, for everyone, including the driver who took
    it.

    Root cause: _parent_visible() in attachments.py has a branch for
    every other attachable transaction table (maintenance_orders,
    trip_tickets, atds, ...) but none for "vehicle_checklists" -- so it
    fell through to its final `return False`, and the download endpoint
    treated every checklist photo as belonging to nobody.
    """
    cl, ju = env
    line = _line(cl)
    _respond(cl, line, "FAILED")
    photo = _post_photo(client, cl, line)
    attachment_id = json.loads(
        photo.get_data(as_text=True))["photo_attachment_id"]

    r = client.get(f"/api/v1/attachments/{attachment_id}/download",
                   headers=_hdr(client))

    assert r.status_code == 200
    assert r.data == JPEG


def test_the_photo_is_refused_to_a_driver_who_cannot_see_the_checklist(
        app, client, env):
    """The fix must not turn the download into an open door -- it should
    follow the SAME visibility VehicleChecklistService.get_visible
    already enforces on the checklist itself."""
    cl, _ju = env
    line = _line(cl)
    _respond(cl, line, "FAILED")
    photo = _post_photo(client, cl, line)
    attachment_id = json.loads(
        photo.get_data(as_text=True))["photo_attachment_id"]

    r = client.get(f"/api/v1/attachments/{attachment_id}/download",
                   headers=_hdr(client, "maria"))

    assert r.status_code == 404


def test_list_rows_do_NOT_carry_the_letterhead(app, client, env):
    """Identical for every row, so carrying it 50 times a page would be
    pure weight on a query just optimised from 104 statements to 3."""
    cl, _ju = env
    rows = json.loads(client.get("/api/v1/checklists",
                                 headers=_hdr(client)).get_data(as_text=True))["items"]
    assert rows
    assert "company" not in rows[0]
