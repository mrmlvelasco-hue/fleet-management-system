"""Attachment storage boundary, upload guards, and scan quality.

Three concerns, all pointing at the same thing: attachment BYTES are a
storage decision, and the application should not be entangled with it.

Production may end up on-premise or in the cloud, and those pull in
different answers -- BLOBs in a managed database are convenient on-prem
and expensive in the cloud, where object storage is an order of
magnitude cheaper per GB and database storage often cannot be shrunk
once grown. The decision does not need making now. The ABILITY to make
it later, without auditing every call site, does.

Scan quality matters for the same documents: text extraction from a
Certificate of Registration needs roughly 300 DPI, and measured against
real 82 DPI scans it managed 42% of fields -- missing engine and chassis
numbers on every sample. Warning at upload is what stops someone
trusting a bad extraction.
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
    vt = VehicleTypeService().create(code="VT-BLOB", name="Car",
                                     category="LIGHT")
    br = BranchService().create(code="BR-BLOB", name="Naic")
    return VehicleService().create(
        vehicle_type_id=vt.id, branch_id=br.id, brand="Toyota",
        model="Vios", year=2009, plate_number="NZO-629",
        conduction_number="CN-BLOB1")


def _png(width=40, height=40):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 210, 220)).save(buf, "PNG")
    return buf.getvalue()


# ── One way in and out of storage ───────────────────────────────────

def test_service_exposes_the_bytes_of_an_attachment(app, db, admin, vehicle):
    from app.core.attachments.attachment_service import AttachmentService
    from app.core.models.attachment import Attachment
    data = _png()
    att = Attachment(reference_table="vehicles", reference_id=vehicle.id,
                     filename="a.png", original_filename="a.png",
                     file_size=len(data), mime_type="image/png",
                     file_data=data)
    db.session.add(att)
    db.session.commit()
    assert AttachmentService().get_bytes(att) == data


def test_get_bytes_returns_none_when_there_is_nothing_stored(app, db, admin,
                                                             vehicle):
    """A row whose bytes are missing must not raise -- callers render a
    placeholder, and a 500 on a document viewer is worse than a gap."""
    from app.core.attachments.attachment_service import AttachmentService
    from app.core.models.attachment import Attachment
    att = Attachment(reference_table="vehicles", reference_id=vehicle.id,
                     filename="gone.png", original_filename="gone.png",
                     file_size=0, mime_type="image/png", file_data=None)
    db.session.add(att)
    db.session.commit()
    assert AttachmentService().get_bytes(att) is None


def test_get_bytes_handles_a_none_attachment(app, db):
    from app.core.attachments.attachment_service import AttachmentService
    assert AttachmentService().get_bytes(None) is None


def test_nothing_outside_the_service_reads_file_data_directly(app):
    """The storage boundary, asserted.

    Every read of the raw column outside the service is a place that
    would need editing to move attachments to object storage. There were
    16 of them; routing them through get_bytes() turns a later cloud
    decision into one service method rather than an audit of the app.
    """
    import pathlib, re
    root = pathlib.Path(app.root_path)
    allowed = {
        "core/attachments/attachment_service.py",
        "core/models/attachment.py",
        # The backfill CLI legitimately operates on the COLUMN itself
        # (copying legacy disk files into it); that is storage
        # maintenance, not application code reading a document.
        "cli.py",
    }
    # Instance reads -- `att.file_data` -- are what couple a caller to
    # where bytes live. Class-level column expressions
    # (`Attachment.file_data.isnot(None)`) are query predicates and are
    # fine anywhere.
    instance_read = re.compile(r"\b(?!Attachment\b)[a-z_][\w]*\.file_data\b")
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        for num, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if instance_read.search(line):
                offenders.append(f"{rel}:{num}")
    assert not offenders, (
        "these read Attachment.file_data directly instead of going "
        "through AttachmentService.get_bytes(): " + ", ".join(offenders))


# ── Upload guards ───────────────────────────────────────────────────

def test_flask_rejects_an_oversized_body_before_it_reaches_the_service(app):
    """AttachmentService already enforces ATTACHMENT_MAX_SIZE_MB, but it
    only runs AFTER Flask has read the whole request into memory. Without
    MAX_CONTENT_LENGTH, a 200MB upload is fully buffered before anything
    rejects it -- the check exists but the machine has already paid for
    the file. MAX_CONTENT_LENGTH makes Werkzeug refuse it at the door.
    """
    limit = app.config.get("MAX_CONTENT_LENGTH")
    assert limit, "MAX_CONTENT_LENGTH is not set"
    # Comfortably above the per-file attachment cap so the service's own
    # message (which names the real limit) is what people normally see.
    assert limit >= 25 * 1024 * 1024


def test_the_service_limit_still_produces_the_readable_error(app, db, admin,
                                                             vehicle):
    from app.core.attachments.attachment_service import (
        AttachmentService, AttachmentError)
    from app.modules.system_admin.models import SystemParameter
    # Created rather than set(): the parameter row does not exist in a
    # fresh database, and set() only updates an existing one -- so the
    # service would have quietly kept its 10MB default and the test
    # would have proved nothing.
    db.session.add(SystemParameter(code="ATTACHMENT_MAX_SIZE_MB", value="1",
                                  data_type="INTEGER", group_name="GENERAL"))
    db.session.commit()

    class _Big:
        filename = "big.png"
        content_type = "image/png"
        def read(self): return b"x" * (2 * 1024 * 1024)
        def seek(self, *a, **k): pass
        def save(self, *a, **k): pass

    with pytest.raises(AttachmentError) as exc:
        AttachmentService().upload(_Big(), "vehicles", vehicle.id)
    assert "MB" in str(exc.value)


# ── Scan quality ────────────────────────────────────────────────────

def test_a_low_resolution_scan_is_reported_as_such(app, db):
    """Measured against real scans: an LTO Certificate of Registration
    at ~82 DPI yielded 42% of fields and missed engine and chassis
    numbers on every sample. Someone needs telling BEFORE they trust an
    extraction, not after."""
    from app.core.attachments.scan_quality import assess_scan
    # A full-width A4 document rendered at 40px across is nowhere near
    # readable.
    result = assess_scan(_png(300, 200), mime_type="image/png")
    assert result["is_low_resolution"] is True
    assert result["estimated_dpi"] < 200
    assert result["message"]


def test_a_good_scan_is_not_flagged(app, db):
    from app.core.attachments.scan_quality import assess_scan
    # ~300 DPI across the short edge of A4 (8.27in x 300 = 2481px).
    result = assess_scan(_png(2480, 3500), mime_type="image/png")
    assert result["is_low_resolution"] is False


def test_assessment_never_raises_on_a_non_image(app, db):
    """PDFs and spreadsheets go through the same upload path."""
    from app.core.attachments.scan_quality import assess_scan
    result = assess_scan(b"%PDF-1.4 not really", mime_type="application/pdf")
    assert result["is_low_resolution"] is False
    assert result["estimated_dpi"] is None


def test_assessment_never_raises_on_corrupt_bytes(app, db):
    from app.core.attachments.scan_quality import assess_scan
    result = assess_scan(b"\x00\x01\x02 garbage", mime_type="image/png")
    assert result["is_low_resolution"] is False


def test_upload_records_the_quality_warning(app, client, db, admin, vehicle):
    """Surfaced in the upload response so the UI can show it at the
    moment of upload, while the person still has the document in hand
    and can rescan."""
    _login = client.post("/login", data={"username": "admin",
                                         "password": "Testpass123!"},
                         follow_redirects=True)
    resp = client.post("/master/attachments/upload", data={
        "reference_table": "vehicles", "reference_id": str(vehicle.id),
        "document_type": "CR",
        "file": (io.BytesIO(_png(300, 200)), "cr_scan.png"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["scan_warning"], "no low-resolution warning returned"
