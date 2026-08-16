"""Attachment document types — labelling the OR and CR.

The OR (Official Receipt) and CR (Certificate of Registration) are the
two documents people actually come looking for on a vehicle, and both
arrive here as scans. Until now Attachment recorded only a filename, so
nothing in the system knew which file was which -- the vehicle report
dumped every attachment into one undifferentiated gallery and the only
clue was whatever the uploader happened to name the file.

The type is a Lookup, not a hardcoded list, so the client can add
document types (insurance policy, deed of sale, LTO inspection report)
without a code release -- the same rule the rest of the app follows.

Nullable throughout: attachments uploaded before this existed have no
type and must keep working exactly as they did.
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
    vt = VehicleTypeService().create(code="VT-DT", name="Truck",
                                     category="HEAVY")
    br = BranchService().create(code="BR-DT", name="Carmona")
    return VehicleService().create(
        vehicle_type_id=vt.id, branch_id=br.id, brand="Isuzu",
        model="Elf", year=2020, plate_number="DOC-1111",
        conduction_number="CN-DT1")


def _png():
    return (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


# ── The lookup type exists and carries OR and CR ────────────────────

def test_document_type_lookup_offers_or_and_cr(app, db):
    from app.modules.system_admin.services.lookup_service import (
        LookupService)
    codes = [row.code for row in
             LookupService().get_by_type_with_fallback("ATTACHMENT_DOC_TYPE")]
    assert "OR" in codes
    assert "CR" in codes


def test_document_types_are_described_not_just_coded(app, db):
    """"CR" means nothing to someone who hasn't been shown -- the
    dropdown needs the full name."""
    from app.modules.system_admin.services.lookup_service import (
        LookupService)
    rows = LookupService().get_by_type_with_fallback("ATTACHMENT_DOC_TYPE")
    by_code = {r.code: r.description for r in rows}
    assert "Official Receipt" in by_code["OR"]
    assert "Certificate of Registration" in by_code["CR"]


# ── The column ──────────────────────────────────────────────────────

def test_attachment_can_carry_a_document_type(app, db, admin, vehicle):
    from app.core.models.attachment import Attachment
    att = Attachment(reference_table="vehicles", reference_id=vehicle.id,
                     filename="cr.png", original_filename="cr.png",
                     file_size=10, mime_type="image/png",
                     document_type="CR")
    db.session.add(att)
    db.session.commit()
    assert db.session.get(Attachment, att.id).document_type == "CR"


def test_document_type_is_optional(app, db, admin, vehicle):
    """Everything already uploaded has no type and must keep working."""
    from app.core.models.attachment import Attachment
    att = Attachment(reference_table="vehicles", reference_id=vehicle.id,
                     filename="misc.png", original_filename="misc.png",
                     file_size=10, mime_type="image/png")
    db.session.add(att)
    db.session.commit()
    assert db.session.get(Attachment, att.id).document_type is None


# ── Upload carries it through ───────────────────────────────────────

def _login(client):
    return client.post("/login", data={"username": "admin",
                                       "password": "Testpass123!"},
                       follow_redirects=True)


def test_upload_stores_the_chosen_document_type(app, client, db, admin,
                                                vehicle):
    from app.core.models.attachment import Attachment
    _login(client)
    resp = client.post("/master/attachments/upload", data={
        "reference_table": "vehicles", "reference_id": str(vehicle.id),
        "document_type": "CR",
        "file": (io.BytesIO(_png()), "scan.png"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    att = db.session.get(Attachment, resp.get_json()["id"])
    assert att.document_type == "CR"


def test_upload_without_a_document_type_still_works(app, client, db, admin,
                                                    vehicle):
    _login(client)
    resp = client.post("/master/attachments/upload", data={
        "reference_table": "vehicles", "reference_id": str(vehicle.id),
        "file": (io.BytesIO(_png()), "scan2.png"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200


def test_upload_rejects_an_unrecognised_document_type(app, client, db,
                                                      admin, vehicle):
    """The value arrives from a form field, so it has to be validated
    against the Lookup rather than trusted."""
    _login(client)
    resp = client.post("/master/attachments/upload", data={
        "reference_table": "vehicles", "reference_id": str(vehicle.id),
        "document_type": "NOT-A-REAL-TYPE",
        "file": (io.BytesIO(_png()), "scan3.png"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# ── The report groups by type ───────────────────────────────────────

def test_vehicle_print_labels_the_cr(app, client, db, admin, vehicle):
    from app.core.models.attachment import Attachment
    db.session.add(Attachment(
        reference_table="vehicles", reference_id=vehicle.id,
        filename="a.png", original_filename="scan-001.png",
        file_size=10, mime_type="image/png", document_type="CR"))
    db.session.commit()
    _login(client)
    html = client.get(
        f"/master/vehicles/{vehicle.id}/print").get_data(as_text=True)
    assert "Certificate of Registration" in html


def test_vehicle_print_still_shows_untyped_attachments(app, client, db,
                                                       admin, vehicle):
    """Nothing uploaded before this feature may disappear from the
    report just because it has no type."""
    from app.core.models.attachment import Attachment
    db.session.add(Attachment(
        reference_table="vehicles", reference_id=vehicle.id,
        filename="b.png", original_filename="legacy-photo.png",
        file_size=10, mime_type="image/png"))
    db.session.commit()
    _login(client)
    html = client.get(
        f"/master/vehicles/{vehicle.id}/print").get_data(as_text=True)
    assert "legacy-photo.png" in html
