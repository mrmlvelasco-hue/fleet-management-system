"""The "Open this document" link in notification emails (2026-11-03).

Michael: "for the email notification can you check why in email the
document is not link or click please check my template for email."

Root cause: get_view_url() built its link with Flask's own url_for(),
which (a) never passed _external=True, so the href was a bare path
like "/transactions/atd/2" with no scheme or host -- meaningless in an
email, which has no "current page" to resolve a relative link
against -- and (b) pointed at the old server-rendered Jinja page
(transactions.atd_detail) rather than the React app at /atd/:id,
which is where the people actually clicking this link work day to
day (approvals, the mobile app, etc.).

Fix: a new System Parameter, APP_PUBLIC_URL (System Administration ->
System Parameters, group NOTIFICATIONS), holds the React app's public
base URL. get_view_url() now builds an absolute link into the React
app from that base plus a per-reference_table path -- and returns
None (omit the link entirely) rather than ever emitting a broken one,
whenever the parameter isn't set or the table has no known page.

maintenance_invoices has no page of its own (it's a panel on its
Maintenance Order's detail page), so it resolves to its PARENT MO's
URL instead.
"""
import pytest

from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)


def _set_public_url(value):
    from app.modules.system_admin.models import SystemParameter
    from app.extensions import db
    row = SystemParameter.query.filter_by(code="APP_PUBLIC_URL").first()
    if row is None:
        db.session.add(SystemParameter(
            code="APP_PUBLIC_URL", value=value, data_type="STRING",
            group_name="NOTIFICATIONS", description="test"))
    else:
        row.value = value
    db.session.commit()


def test_app_public_url_parameter_is_seeded(app, db):
    """System Administration lists ROWS -- an unseeded parameter never
    appears and nobody can configure it (the same lesson already
    learned for REQUIRE_DRIVER_LICENSE)."""
    from app.cli import _seed_system_parameters
    _seed_system_parameters()
    assert SystemParameterService().get("APP_PUBLIC_URL") is not None


def test_returns_none_when_not_configured(app, db):
    """No broken link is better than one that goes nowhere -- an
    unconfigured install must omit the link, not emit a dead one."""
    from app.core.reference_resolver import get_view_url
    _set_public_url("")
    assert get_view_url("maintenance_orders", 1) is None


def test_returns_an_absolute_url_once_configured(app, db):
    from app.core.reference_resolver import get_view_url
    _set_public_url("https://fms.example.com")
    assert get_view_url("maintenance_orders", 42) == (
        "https://fms.example.com/maintenance-orders/42")


def test_strips_a_trailing_slash_on_the_configured_base(app, db):
    from app.core.reference_resolver import get_view_url
    _set_public_url("https://fms.example.com/")
    assert get_view_url("maintenance_orders", 42) == (
        "https://fms.example.com/maintenance-orders/42")


def test_every_registered_reference_table_resolves(app, db):
    """One entry per module already registered in reference_resolver's
    own _REGISTRY, matching the React route each module actually
    uses (confirmed against src/App.tsx)."""
    from app.core.reference_resolver import get_view_url
    _set_public_url("https://fms.example.com")
    expected = {
        "maintenance_orders": "/maintenance-orders/7",
        "purchase_requests": "/purchase-requests/7",
        "trip_tickets": "/trip-tickets/7",
        "authority_to_drives": "/atd/7",
        "vehicle_movements": "/vehicle-movements/7",
        "vehicle_registrations": "/vehicle-registrations/7",
        "tire_transactions": "/tire-transactions/7",
        "battery_transactions": "/battery-transactions/7",
    }
    for table, path in expected.items():
        assert get_view_url(table, 7) == f"https://fms.example.com{path}", table


def test_an_unregistered_table_returns_none(app, db):
    from app.core.reference_resolver import get_view_url
    _set_public_url("https://fms.example.com")
    assert get_view_url("no_such_table", 1) is None


def test_maintenance_invoice_resolves_to_its_parent_mo(app, db):
    """An invoice has no page of its own -- it's a line on its MO's Cost
    Summary panel, so its email link must go there, not to a 404."""
    from datetime import date
    from app.core.reference_resolver import get_view_url
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.reference.service import (
        MaintenanceTypeService, VehicleTypeService)
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.vendor.service import VendorService
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    from app.modules.transactions.maintenance_invoice.models import (
        MaintenanceInvoice)
    from app.extensions import db

    _set_public_url("https://fms.example.com")
    branch = BranchService().create(code="BR-INV", name="Invoice Branch")
    vt = VehicleTypeService().create(code="LV-INV", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="INV-CM", name="Corrective",
                                         category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2023,
        branch_id=branch.id, conduction_number="INV-001")
    vendor = VendorService().create(code="VEN-INV", name="Invoice Vendor")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), odometer_at_service=1000, user=None)
    invoice = MaintenanceInvoice(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-1", invoice_date=date.today())
    db.session.add(invoice)
    db.session.commit()

    assert get_view_url("maintenance_invoices", invoice.id) == (
        f"https://fms.example.com/maintenance-orders/{order.id}")


def test_a_missing_maintenance_invoice_returns_none(app, db):
    from app.core.reference_resolver import get_view_url
    _set_public_url("https://fms.example.com")
    assert get_view_url("maintenance_invoices", 999999) is None


# ── seeded template HTML is guarded the same way the text body already was ──

def test_freshly_seeded_template_guards_the_link(app, db):
    """A brand-new install must never show a dead <a href=""> before
    APP_PUBLIC_URL is configured -- the plain-text body already had
    this guard; the HTML body did not."""
    from app.modules.system_admin.models import EmailTemplate
    from app.cli import _seed_email_templates
    _seed_email_templates()
    tmpl = EmailTemplate.query.filter_by(event_code="approved_level").first()
    assert tmpl is not None
    assert '{% if view_url %}' in tmpl.body_html
    assert 'Open this document' in tmpl.body_html


# ── flask notifications fix-dead-view-url-link ──────────────────────────

def _old_unguarded_template(event_code="approved_level"):
    from app.extensions import db
    from app.modules.system_admin.models import EmailTemplate
    tmpl = EmailTemplate(
        event_code=event_code, name="Approval Level Passed",
        subject="[FMS] Progressed: {{ document_number }}",
        body_html=('<p>Hello {{ recipient_name }},</p>'
                   '<p>A document you submitted has passed an approval '
                   'level.</p>'
                   '<p><strong>{{ document_number }}</strong></p>'
                   '<p><a href="{{ view_url }}">Open this document</a></p>'),
        body_text="Hello {{ recipient_name }},\n\n{{ document_number }}")
    db.session.add(tmpl)
    db.session.commit()
    return tmpl


def test_fix_command_dry_run_leaves_the_database_unchanged(client, db):
    tmpl = _old_unguarded_template()
    runner = client.application.test_cli_runner()
    result = runner.invoke(
        args=["notifications", "fix-dead-view-url-link", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would fix 1" in result.output
    db.session.expire(tmpl)
    assert '{% if view_url %}' not in tmpl.body_html


def test_fix_command_commit_guards_the_old_unconditional_link(client, db):
    tmpl = _old_unguarded_template()
    runner = client.application.test_cli_runner()
    result = runner.invoke(
        args=["notifications", "fix-dead-view-url-link", "--commit"])
    assert result.exit_code == 0, result.output
    assert "Fixed 1" in result.output
    db.session.expire(tmpl)
    assert '{% if view_url %}<p><a href="{{ view_url }}">Open this document</a></p>{% endif %}' \
        in tmpl.body_html


def test_fix_command_leaves_a_customized_template_untouched(client, db):
    """An admin who already edited the link (or removed it) must never
    have their wording silently overwritten."""
    from app.extensions import db as _db
    tmpl = _old_unguarded_template()
    tmpl.body_html = tmpl.body_html.replace(
        "Open this document", "Click here to view the request")
    _db.session.commit()
    custom_html = tmpl.body_html
    runner = client.application.test_cli_runner()
    result = runner.invoke(
        args=["notifications", "fix-dead-view-url-link", "--commit"])
    assert result.exit_code == 0, result.output
    assert "Skipped 1" in result.output
    _db.session.expire(tmpl)
    assert tmpl.body_html == custom_html


def test_fix_command_is_idempotent(client, db):
    _old_unguarded_template()
    runner = client.application.test_cli_runner()
    runner.invoke(args=["notifications", "fix-dead-view-url-link", "--commit"])
    second = runner.invoke(
        args=["notifications", "fix-dead-view-url-link", "--commit"])
    assert second.exit_code == 0, second.output
    assert "Fixed 0" in second.output
