"""Print Templates — the editable Oath of Undertaking.

The Vehicle Assignment Memo is issued with an Oath of Undertaking: the
assignee signs it, and it lists the obligations that come with holding a
company vehicle. Its wording is a company policy document, so it has to
be editable by the fleet manager without a code release -- the clauses
change as policy changes.

Held in its own table rather than folded into Email Templates, which
would put a printed legal document behind a screen labelled "Email".

Tokens follow the same idea as PM work-description tokens: a small set
of placeholders resolved against the order being printed, with the
available list shown on the edit screen so nobody has to guess.
"""
import pytest
from datetime import date

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
def order(app, db, admin):
    """An Assignment order with an assignee, so the VAM applies."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.driver.service import DriverService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from tests.conftest import tiny_jpeg_file

    vt = VehicleTypeService().create(code="VT-OATH", name="Van",
                                     category="LIGHT")
    br = BranchService().create(code="BR-OATH", name="Bacolod Plant")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, branch_id=br.id, brand="Toyota",
        model="Avanza", year=2016, plate_number="NBW-1226",
        conduction_number="CN-OATH1", color="Silver Metallic",
        engine_number="1NRF025658", chassis_number="MHKMSEE1F0K000203")
    driver = DriverService().create(
        employee_number="0509511", assignee_type="EMPLOYEE",
        first_name="Girard Paul", last_name="Doronila",
        branch_id=br.id, position="Manager, Territory",
        complete_address="Rizal St. Talisay City Negros Occidental",
        phone="(0919) 6694-789", photo_file=tiny_jpeg_file())
    mo = MaintenanceOrder(
        document_number="VAM-009667", vehicle_id=vehicle.id,
        driver_id=driver.id, order_category="OPERATIONAL",
        scheduled_date=date(2020, 10, 30), completed_date=date(2020, 10, 30),
        odometer_at_service=12345, status="COMPLETED",
        assignment_classification="TOOL_OF_THE_TRADE",
        requested_by=admin.id)
    db.session.add(mo)
    db.session.commit()
    return mo


def _login(client):
    return client.post("/login", data={"username": "admin",
                                       "password": "Testpass123!"},
                       follow_redirects=True)


# ── The template store ──────────────────────────────────────────────

def test_oath_template_is_seeded(app, db):
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    tpl = PrintTemplateService().get("OATH_OF_UNDERTAKING")
    assert tpl is not None
    assert tpl.body_html.strip(), "the Oath ships with no default body"


def test_template_carries_a_paper_size(app, db):
    """Print output has to match the paper actually loaded, so the size
    is part of the template rather than hardcoded in the stylesheet."""
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    tpl = PrintTemplateService().get("OATH_OF_UNDERTAKING")
    assert tpl.paper_size in ("A4", "LETTER", "LEGAL")


def test_paper_size_is_validated_on_save(app, db):
    from app.cli import _seed_print_templates
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService, InvalidPaperSizeError)
    _seed_print_templates()
    svc = PrintTemplateService()
    tpl = svc.get("OATH_OF_UNDERTAKING")
    with pytest.raises(InvalidPaperSizeError):
        svc.update(tpl.id, body_html="x", paper_size="FOOLSCAP-ish")


def test_editing_the_body_persists(app, db):
    from app.cli import _seed_print_templates
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    _seed_print_templates()
    svc = PrintTemplateService()
    tpl = svc.get("OATH_OF_UNDERTAKING")
    svc.update(tpl.id, body_html="<p>House rules v2</p>", paper_size="LEGAL")
    again = svc.get("OATH_OF_UNDERTAKING")
    assert "House rules v2" in again.body_html
    assert again.paper_size == "LEGAL"


def test_a_missing_template_never_breaks_the_printout(app, db):
    """Resolution must degrade: a printout is a live operational
    document and must not 500 because a template row was deleted."""
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    assert PrintTemplateService().get("NO_SUCH_TEMPLATE") is None


# ── Token resolution ────────────────────────────────────────────────

def test_tokens_resolve_against_the_order(app, db, order):
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    body = ("Assigned to {ASSIGNEE_NAME} — {VEHICLE_MODEL} "
            "plate {PLATE_NO}, colour {BODY_COLOR}.")
    out = PrintTemplateService().render(body, order=order)
    assert "Girard Paul Doronila" in out
    assert "Toyota Avanza" in out
    assert "NBW-1226" in out
    assert "Silver Metallic" in out


def test_unknown_tokens_are_left_alone(app, db, order):
    """A typo in a hand-edited template must show as itself, not vanish
    -- a silently blank clause in a signed undertaking is worse than a
    visibly wrong one."""
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    out = PrintTemplateService().render("Hello {NOT_A_TOKEN}", order=order)
    assert "{NOT_A_TOKEN}" in out


def test_missing_values_render_as_a_dash_not_the_word_none(app, db, admin):
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    bare = MaintenanceOrder(order_category="OPERATIONAL",
                            scheduled_date=date(2026, 1, 1),
                            status="DRAFT", requested_by=admin.id)
    out = PrintTemplateService().render("[{PLATE_NO}]", order=bare)
    assert "None" not in out


def test_the_token_list_is_published_for_the_edit_screen(app, db):
    """The fleet manager edits this text; the tokens have to be
    discoverable rather than folklore."""
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    tokens = dict(PrintTemplateService().available_tokens())
    for t in ("{ASSIGNEE_NAME}", "{PLATE_NO}", "{VEHICLE_MODEL}",
              "{VAM_NO}", "{CR_NO}", "{OR_NO}"):
        assert t in tokens, f"{t} is not published"
        assert tokens[t], f"{t} has no description"


# ── It reaches the VAM printout ─────────────────────────────────────

def test_vam_print_includes_the_oath(app, client, db, order):
    _login(client)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam"
    ).get_data(as_text=True)
    assert 'class="oath-body"' in html


def test_oath_body_is_token_resolved_in_the_printout(app, client, db, order):
    _login(client)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam"
    ).get_data(as_text=True)
    assert "Girard Paul Doronila" in html
    assert "{ASSIGNEE_NAME}" not in html, "a raw token reached the paper"


def test_printout_carries_the_conforme_page(app, client, db, order):
    """Page 3 of the paper form: the assignee's own details, signed."""
    _login(client)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam"
    ).get_data(as_text=True)
    assert "CONFORME" in html
    assert "0509511" in html                      # employee number
    assert "Manager, Territory" in html           # position
    assert "Rizal St. Talisay" in html            # address


def test_printout_uses_the_configured_paper_size(app, client, db, order):
    from app.cli import _seed_print_templates
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    _seed_print_templates()
    svc = PrintTemplateService()
    svc.update(svc.get("OATH_OF_UNDERTAKING").id, paper_size="LEGAL")
    _login(client)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam"
    ).get_data(as_text=True)
    assert "size: Legal portrait" in html or "size: legal" in html.lower()


def test_vam_can_still_be_printed_without_the_oath(app, client, db, order):
    """A reprint for filing usually doesn't need the undertaking again."""
    _login(client)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam?oath=0"
    ).get_data(as_text=True)
    assert "VEHICLE ASSIGNMENT MEMO" in html
    # The memo's own legal paragraph mentions the undertaking by name,
    # so the phrase is a poor marker; the appended SECTION is what must
    # be absent.
    assert 'class="oath-body"' not in html
    assert "CONFORME" not in html


# ── Front / back vehicle photos ─────────────────────────────────────

def test_front_and_back_photo_document_types_exist(app, db):
    """Page 3 shows the vehicle front and back. A generic PHOTO type
    can't tell them apart, so each gets its own Lookup code."""
    from app.modules.system_admin.services.lookup_service import LookupService
    codes = [r.code for r in
             LookupService().get_by_type_with_fallback("ATTACHMENT_DOC_TYPE")]
    assert "PHOTO_FRONT" in codes
    assert "PHOTO_BACK" in codes


def test_printout_shows_the_front_and_back_photos(app, client, db, order):
    from app.core.models.attachment import Attachment
    for dt, name in (("PHOTO_FRONT", "front.png"), ("PHOTO_BACK", "back.png")):
        db.session.add(Attachment(
            reference_table="vehicles", reference_id=order.vehicle_id,
            filename=name, original_filename=name, file_size=10,
            mime_type="image/png", document_type=dt))
    db.session.commit()
    _login(client)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam"
    ).get_data(as_text=True)
    assert "VehicleAttachment_Front" in html
    assert "VehicleAttachment_Back" in html


def test_printout_survives_a_vehicle_with_no_photos(app, client, db, order):
    _login(client)
    resp = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam")
    assert resp.status_code == 200
