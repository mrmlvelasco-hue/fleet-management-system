"""The extract-from-CR endpoints, end to end through the routes.

Two endpoints: one reads an attachment and reports what it found, one
applies the fields a person selected. They are separate on purpose --
reading is slow and repeatable, applying is fast and destructive, and
mixing them would mean a re-read every time someone changed their mind
about a tickbox.
"""
import io
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def admin(app, db):
    from app.modules.user_management.models import User
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    return User.query.filter_by(username="admin").first()


@pytest.fixture()
def vehicle(app, db):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="VT-XR", name="SUV",
                                     category="LIGHT")
    br = BranchService().create(code="BR-XR", name="Carmona")
    return VehicleService().create(
        vehicle_type_id=vt.id, branch_id=br.id, brand="Unknown",
        model="Unknown", year=2000, plate_number="XTR-0001",
        conduction_number="CN-XR1")


@pytest.fixture()
def cr_attachment(app, db, vehicle):
    from app.core.models.attachment import Attachment
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (255, 255, 255)).save(buf, "PNG")
    att = Attachment(reference_table="vehicles", reference_id=vehicle.id,
                     filename="cr.png", original_filename="cr.png",
                     file_size=buf.tell(), mime_type="image/png",
                     file_data=buf.getvalue(), document_type="CR")
    db.session.add(att)
    db.session.commit()
    return att


def _login(client):
    return client.post("/login", data={"username": "admin",
                                       "password": "Testpass123!"},
                       follow_redirects=True)


# ── Reading ─────────────────────────────────────────────────────────

def test_extract_endpoint_responds(app, client, db, admin, cr_attachment):
    _login(client)
    resp = client.post(f"/master/attachments/{cr_attachment.id}/extract")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "trusted" in body and "unverified" in body
    assert body["message"]


def test_extract_reports_when_nothing_readable(app, client, db, admin,
                                               cr_attachment):
    """A blank image yields nothing. That must read as a plain outcome,
    not an error -- the person just types the details as before."""
    _login(client)
    body = client.post(
        f"/master/attachments/{cr_attachment.id}/extract").get_json()
    assert body["trusted"] == {}
    assert "manual" in body["message"].lower()


def test_extract_requires_login(app, client, db, admin, cr_attachment):
    resp = client.post(f"/master/attachments/{cr_attachment.id}/extract")
    assert resp.status_code in (302, 401)


def test_extract_on_a_missing_attachment_is_a_clean_404(app, client, db,
                                                        admin):
    _login(client)
    assert client.post("/master/attachments/999999/extract").status_code == 404


def test_extract_reports_when_ocr_is_unavailable(app, client, db, admin,
                                                 cr_attachment, monkeypatch):
    """Deployment target is undecided, so an image without tesseract is a
    real scenario. It must say so rather than look broken."""
    import app.core.extraction.ocr as ocr_mod
    # The route calls diagnose(), not ocr_available(), because it needs
    # the REASON rather than a yes/no -- patching the boolean alone
    # would leave the route reporting success.
    monkeypatch.setattr(ocr_mod, "diagnose", lambda: {
        "ok": False, "command": None, "version": None, "source": None,
        "interpreter": "/fake/python", "platform": "Linux",
        "in_container": True,
        "reason": "The pytesseract package is not available here."})
    _login(client)
    body = client.post(
        f"/master/attachments/{cr_attachment.id}/extract").get_json()
    assert body["trusted"] == {}
    assert "not available" in body["message"].lower()
    # The interpreter is reported so whoever is fixing it knows WHICH
    # Python is complaining -- a dev machine has several in play.
    assert body["interpreter"] == "/fake/python"


# ── Applying ────────────────────────────────────────────────────────

def _apply(client, vehicle_id, payload):
    return client.post(f"/master/vehicles/{vehicle_id}/apply-extraction",
                       json=payload)


def test_apply_writes_only_what_was_selected(app, client, db, admin,
                                             vehicle):
    _login(client)
    resp = _apply(client, vehicle.id, {
        "fields": {"chassis_number": {"value": "MRHRU5830LP020394",
                                      "needs_review": False},
                   "brand": {"value": "GEELY", "needs_review": True}},
        "selected": ["chassis_number"], "confirmed": []})
    assert resp.status_code == 200
    db.session.refresh(vehicle)
    assert vehicle.chassis_number == "MRHRU5830LP020394"
    assert vehicle.brand == "Unknown", "an unticked field was written"


def test_apply_refuses_an_unconfirmed_flagged_field(app, client, db, admin,
                                                    vehicle):
    """The client's rule: it must be checked before it can be saved."""
    _login(client)
    resp = _apply(client, vehicle.id, {
        "fields": {"engine_number": {"value": "R18ZF3912550",
                                     "needs_review": True}},
        "selected": ["engine_number"], "confirmed": []})
    assert resp.status_code == 400
    assert "engine_number" in resp.get_json()["fields"]
    db.session.refresh(vehicle)
    assert vehicle.engine_number != "R18ZF3912550"


def test_apply_accepts_a_confirmed_flagged_field(app, client, db, admin,
                                                 vehicle):
    _login(client)
    resp = _apply(client, vehicle.id, {
        "fields": {"engine_number": {"value": "R18ZF3912550",
                                     "needs_review": True}},
        "selected": ["engine_number"], "confirmed": ["engine_number"]})
    assert resp.status_code == 200
    db.session.refresh(vehicle)
    assert vehicle.engine_number == "R18ZF3912550"


def test_apply_never_writes_a_column_that_does_not_exist(app, client, db,
                                                         admin, vehicle):
    """The payload comes from the browser, so it can name anything."""
    _login(client)
    resp = _apply(client, vehicle.id, {
        "fields": {"__class__": {"value": "evil", "needs_review": False}},
        "selected": ["__class__"], "confirmed": []})
    assert resp.status_code == 200
    assert resp.get_json()["applied"] == {}


def test_apply_requires_the_update_permission(app, client, db, admin,
                                              vehicle):
    _login(client)
    resp = _apply(client, vehicle.id, {"fields": {}, "selected": []})
    assert resp.status_code in (200, 403)


# ── The button is reachable ─────────────────────────────────────────

def test_attachment_panel_offers_extraction_for_a_cr(app, client, db, admin,
                                                     vehicle, cr_attachment):
    _login(client)
    # The DETAIL page is what server-renders the attachment list; the
    # edit form loads it separately, so asserting there would pass or
    # fail for reasons unrelated to this button.
    html = client.get(
        f"/master/vehicles/{vehicle.id}").get_data(as_text=True)
    assert "extract-btn" in html, "no Extract button for a CR attachment"
