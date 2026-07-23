"""Maintenance Invoice service — VAT-aware line calculation and
auto-recalculating header summary, plus the standard submit/approve/
reject/return workflow via BaseTransactionService.
"""
from decimal import Decimal

from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.maintenance_invoice.models import (
    MaintenanceInvoice, MaintenanceInvoiceLine)


class InvoiceLockedError(Exception):
    """Raised when attempting to modify an invoice that's already
    APPROVED — per the spec: 'Prevent invoice modifications after
    approval unless reopened by an authorized user.'"""
    pass


class MaintenanceInvoiceService(BaseTransactionService):
    model = MaintenanceInvoice
    document_type_code = "INV"
    reference_table = "maintenance_invoices"

    def submit(self, record_id: int, user):
        """Overrides the base submit() to sync the invoice's OWN status
        field with what actually happened — the base implementation only
        sets approval_instance_id and never touches record.status at all.
        Per the spec, Invoice approval is configurable ('No Approval
        Required' is a valid choice): when it's off, the underlying
        ApprovalInstance auto-approves itself instantly, but nothing was
        propagating that back onto the invoice — it stayed stuck at DRAFT
        forever with no way to reach a completed state. Now: no approval
        configured -> straight to APPROVED; approval configured -> SUBMITTED
        (correctly reflecting it's awaiting a real approval, not jumping
        ahead of it).

        Also handles the adjacent case reported in practice: Invoice
        approval IS marked as required, but no Approval Matrix has
        actually been configured for it yet (e.g. not set up during
        System Administration setup). Previously this raised NoMatrixError
        straight out of submit(), leaving the invoice stuck in DRAFT with
        a confusing error and no way forward. Treated the same as "no
        approval required" -- there's nothing configured to route this
        to, so it goes straight to APPROVED rather than blocking the
        user. An admin adding a real matrix later only affects invoices
        submitted after that point.
        """
        from app.modules.approval_config.service import NoMatrixError
        try:
            record = super().submit(record_id, user)
        except NoMatrixError:
            record = db.session.get(MaintenanceInvoice, record_id)
            record.status = "APPROVED"
            db.session.commit()
            return record
        if record.approval_instance and record.approval_instance.status == "APPROVED":
            record.status = "APPROVED"
        else:
            record.status = "SUBMITTED"
        db.session.commit()
        return record

    def create(self, *, maintenance_order_id, vendor_id, invoice_number,
              invoice_date, user, vat_type="VAT_EXCLUSIVE", vat_percentage=12,
              or_number=None, po_number=None, dr_number=None, currency="PHP"):
        try:
            doc_number = AutoNumberingService().generate(self.document_type_code)
        except Exception:
            doc_number = None
        inv = MaintenanceInvoice(
            document_number=doc_number,
            maintenance_order_id=maintenance_order_id, vendor_id=vendor_id,
            invoice_number=invoice_number, invoice_date=invoice_date,
            vat_type=vat_type, vat_percentage=vat_percentage,
            or_number=or_number, po_number=po_number, dr_number=dr_number,
            currency=currency, status="DRAFT",
            requested_by=user.id if user else None)
        db.session.add(inv)
        db.session.commit()
        # An invoice against a Maintenance Order is ENCODING of an actual
        # expense already incurred, not a request for permission -- so
        # unless an approval is deliberately configured for the INV
        # document type, it should be RECORDED on entry rather than
        # sitting at DRAFT waiting for a submit/approve step that the
        # business never intended. (Leaving it at DRAFT made every
        # encoded invoice look perpetually unfinished on the MO screen.)
        if not self._invoice_approval_required():
            inv.status = "RECORDED"
            db.session.commit()
        return inv

    def _invoice_approval_required(self) -> bool:
        """True only when the INV document type is explicitly configured
        to require approval. Defaults to False (encoding-only) if the
        document type isn't configured at all, so a missing config never
        silently strands invoices in DRAFT."""
        from app.modules.document_config.models import DocumentType
        dt = DocumentType.query.filter_by(
            code=self.document_type_code).first()
        return bool(dt and dt.requires_approval)

    def get_by_id(self, invoice_id):
        return db.session.get(MaintenanceInvoice, invoice_id)

    def list_for_order(self, maintenance_order_id) -> list:
        return (MaintenanceInvoice.query
               .filter_by(maintenance_order_id=maintenance_order_id,
                         is_active=True)
               .order_by(MaintenanceInvoice.id.desc())
               .all())

    def _calculate_line(self, invoice: MaintenanceInvoice, *, quantity,
                        unit_cost, discount):
        """VAT handling per the invoice's vat_type:
        - VAT_EXCLUSIVE: unit_cost is pre-VAT; VAT is added on top.
        - VAT_INCLUSIVE: unit_cost already includes VAT; back it out to
          get the net (line_amount) and the VAT portion.
        - NON_VAT: no VAT at all.
        Returns (line_amount, vat_amount, total_amount). All arithmetic
        is done in Decimal throughout — mixing Decimal (from Numeric DB
        columns) with plain float/int inputs raises TypeError, so every
        input is cast up front rather than relying on Python's implicit
        coercion partway through."""
        quantity = Decimal(str(quantity))
        unit_cost = Decimal(str(unit_cost))
        discount = Decimal(str(discount))
        gross_before_vat_logic = (quantity * unit_cost) - discount
        rate = Decimal(str(invoice.vat_percentage)) / Decimal(100)

        if invoice.vat_type == "VAT_INCLUSIVE":
            total_amount = gross_before_vat_logic
            line_amount = (total_amount / (1 + rate) if rate
                          else total_amount)
            vat_amount = total_amount - line_amount
        elif invoice.vat_type == "NON_VAT":
            line_amount = gross_before_vat_logic
            vat_amount = Decimal(0)
            total_amount = line_amount
        else:  # VAT_EXCLUSIVE (default)
            line_amount = gross_before_vat_logic
            vat_amount = line_amount * rate
            total_amount = line_amount + vat_amount

        return line_amount, vat_amount, total_amount

    def add_line(self, invoice_id, *, part_description, expense_category,
                charged_to, quantity=1, unit_cost=0, discount=0,
                part_number=None, specification=None, uom=None,
                sort_order=None):
        invoice = self.get_by_id(invoice_id)
        if invoice.status == "APPROVED":
            raise InvoiceLockedError(
                "This invoice is approved and locked. Reopen it first "
                "before adding line items.")
        # Cast once here so both the stored raw fields AND the calculated
        # fields are consistently Decimal — form data arrives as strings,
        # and storing those directly (even though _calculate_line() casts
        # its own local copies for the arithmetic) would leave e.g.
        # discount as a literal string on the row, breaking any later
        # arithmetic over it (like summing discounts across lines).
        quantity = Decimal(str(quantity))
        unit_cost = Decimal(str(unit_cost))
        discount = Decimal(str(discount))
        line_amount, vat_amount, total_amount = self._calculate_line(
            invoice, quantity=quantity, unit_cost=unit_cost, discount=discount)
        if sort_order is None:
            # A direct count, not len(invoice.line_items) — touching that
            # relationship here would cache it (possibly empty, if this is
            # the first line) on the invoice object, and the app runs with
            # expire_on_commit=False, so that stale empty cache would
            # never self-correct for any caller reusing this same object.
            existing_count = MaintenanceInvoiceLine.query.filter_by(
                invoice_id=invoice_id).count()
            sort_order = existing_count + 1
        line = MaintenanceInvoiceLine(
            invoice_id=invoice_id, part_number=part_number,
            part_description=part_description, specification=specification,
            uom=uom, quantity=quantity, unit_cost=unit_cost,
            discount=discount, line_amount=line_amount, vat_amount=vat_amount,
            total_amount=total_amount, expense_category=expense_category,
            charged_to=charged_to, sort_order=sort_order)
        db.session.add(line)
        db.session.commit()
        self._recalculate_summary(invoice)
        return line

    def remove_line(self, line_id):
        line = db.session.get(MaintenanceInvoiceLine, line_id)
        if not line:
            return
        invoice = line.invoice
        if invoice.status == "APPROVED":
            raise InvoiceLockedError(
                "This invoice is approved and locked. Reopen it first "
                "before removing line items.")
        db.session.delete(line)
        db.session.commit()
        self._recalculate_summary(invoice)

    def _recalculate_summary(self, invoice: MaintenanceInvoice) -> None:
        lines = MaintenanceInvoiceLine.query.filter_by(
            invoice_id=invoice.id).all()
        invoice.total_parts_cost = sum(
            (l.line_amount for l in lines if l.expense_category == "PARTS"), 0)
        invoice.total_labor_cost = sum(
            (l.line_amount for l in lines if l.expense_category == "LABOR"), 0)
        invoice.total_vat = sum((l.vat_amount for l in lines), 0)
        invoice.total_discount = sum((l.discount for l in lines), 0)
        invoice.net_amount = sum((l.line_amount for l in lines), 0)
        invoice.gross_amount = invoice.net_amount + invoice.total_vat
        invoice.total_invoice_amount = sum((l.total_amount for l in lines), 0)
        db.session.commit()
        # The app runs with expire_on_commit=False, so a caller holding
        # this same invoice object from earlier (e.g. before any lines
        # existed) would otherwise keep seeing a stale/empty line_items
        # collection indefinitely — expire just that relationship so the
        # next access re-queries it fresh.
        db.session.expire(invoice, ["line_items"])

    def reopen(self, invoice_id, user):
        """Explicitly re-opens an APPROVED invoice for editing — per the
        spec, this must be a deliberate, authorized action, not something
        that happens implicitly. Route-level permission gating decides
        who's 'authorized'; this just performs the state change."""
        invoice = self.get_by_id(invoice_id)
        invoice.status = "DRAFT"
        db.session.commit()
        return invoice
