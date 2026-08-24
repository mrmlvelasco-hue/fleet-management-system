"""Maintenance Invoice API.

Flask has nine invoice routes; the API had none, so the whole module was
unreachable from React. That is what blocks completing a Maintenance
Order: the actual cost comes from invoices, and without them an order
sits IN_PROGRESS with nowhere to record what was spent.

Lines carry `expense_category` and `charged_to`, which is where the MO
Cost Summary's category rows come from -- so this is the missing half of
a panel that already exists.

Every rule stays in MaintenanceInvoiceService: numbering, VAT
calculation, the APPROVED lock, and _recalculate_summary. The API
computes no totals of its own -- a second implementation of money
arithmetic is a second answer.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

#: Labels match the Jinja form's, so both apps report the same problem
#: in the same words.
_COERCER = Coercer(
    ints={"maintenance_order_id": "Maintenance Order",
          "vendor_id": "Supplier / Vendor"},
    # Reported by the client: creating an invoice 500'd with
    #   ValueError: Unknown format code 'f' for object of type 'str'
    # vat_percentage was never added here, so the JSON string "12"
    # reached the Numeric column uncoerced. SQLAlchemy silently
    # converts it on FLUSH, so the write itself succeeded -- the row was
    # correct -- but the in-memory object still held the string, and
    # _money() tried to format that string with :.2f while building the
    # 201 response. Same fault class vehicles had before the shared
    # Coercer existed: a write that succeeds and then fails while
    # reporting its own result.
    decimals={"vat_percentage": "VAT %"},
    dates={"invoice_date": "Invoice Date"},
)

_HEADER_FIELDS = [
    "maintenance_order_id", "vendor_id", "invoice_number", "invoice_date",
    "vat_type", "vat_percentage", "or_number", "po_number", "dr_number",
    "currency",
]


def _validation(exc):
    field = getattr(exc, "field", "_")
    return jsonify({"error": "validation", "message": str(exc),
                    "fields": {field: str(exc)}}), 400


def _bad(message, field=None):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field or "_": message}}), 400


def _not_found(label="Invoice"):
    return jsonify({"error": "not_found",
                    "message": f"{label} not found."}), 404


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _money(v):
    return None if v is None else f"{v:.2f}"


def _line_json(line):
    return {
        "id": line.id,
        "part_number": line.part_number,
        "part_description": line.part_description,
        "specification": line.specification,
        "uom": line.uom,
        "quantity": _money(line.quantity),
        "unit_cost": _money(line.unit_cost),
        "discount": _money(line.discount),
        "line_amount": _money(line.line_amount),
        "vat_amount": _money(line.vat_amount),
        "total_amount": _money(line.total_amount),
        "expense_category": line.expense_category,
        "charged_to": line.charged_to,
        "sort_order": line.sort_order,
    }


def _invoice_json(inv, detail=False):
    data = {
        "id": inv.id,
        "document_number": inv.document_number,
        "maintenance_order_id": inv.maintenance_order_id,
        "vendor_id": inv.vendor_id,
        "vendor": inv.vendor.name if inv.vendor else None,
        # Feeds the Vendor Information card the client's own invoice
        # detail template shows. The summary/list payload deliberately
        # keeps only the name; these travel on the DETAIL payload only,
        # matching where Flask puts them.
        "vendor_code": inv.vendor.code if inv.vendor else None,
        "vendor_contact_person": (
            inv.vendor.contact_person if inv.vendor else None),
        "vendor_phone": inv.vendor.phone if inv.vendor else None,
        "vendor_email": inv.vendor.email if inv.vendor else None,
        "vendor_address": inv.vendor.address if inv.vendor else None,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date.isoformat()
        if inv.invoice_date else None,
        "or_number": inv.or_number,
        "po_number": inv.po_number,
        "dr_number": inv.dr_number,
        "vat_type": inv.vat_type,
        "vat_percentage": _money(inv.vat_percentage),
        "currency": inv.currency,
        "status": inv.status,
        "total_parts_cost": _money(inv.total_parts_cost),
        "total_labor_cost": _money(inv.total_labor_cost),
        "total_vat": _money(inv.total_vat),
        "total_discount": _money(inv.total_discount),
        "gross_amount": _money(inv.gross_amount),
        "net_amount": _money(inv.net_amount),
        "total_invoice_amount": _money(inv.total_invoice_amount),
        # APPROVED invoices are locked by the service. Sent as a
        # decision so the client disables entry rather than letting
        # someone type a line that will be refused on submit.
        "locked": inv.status == "APPROVED",
    }
    if detail:
        data["lines"] = [_line_json(l) for l in sorted(
            inv.line_items, key=lambda x: (x.sort_order or 0, x.id))]
    return data


@bp.route("/invoices", methods=["POST"])
@api_auth_required("maintenanceinvoice.create")
def create_invoice(api_user):
    """Invoice header. Line items are added afterwards, as in Flask --
    the header must exist before a line can reference it."""
    from app.modules.transactions.maintenance_invoice.service import (
        MaintenanceInvoiceService)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, _HEADER_FIELDS)
    except FieldValueError as exc:
        return _validation(exc)

    for required in ("maintenance_order_id", "vendor_id", "invoice_number",
                     "invoice_date"):
        if not fields.get(required):
            return _bad(f"{required.replace('_', ' ').title()} is required.",
                        required)

    try:
        inv = MaintenanceInvoiceService().create(user=api_user, **fields)
    except Exception as exc:
        return _conflict(str(exc))
    return jsonify(_invoice_json(inv, detail=True)), 201


@bp.route("/invoices/<int:iid>", methods=["GET"])
@api_auth_required("maintenanceinvoice.view")
def invoice_detail(api_user, iid):
    from app.modules.transactions.maintenance_invoice.models import (
        MaintenanceInvoice)

    inv = MaintenanceInvoice.query.filter_by(id=iid).first()
    if inv is None:
        return _not_found()
    return jsonify(_invoice_json(inv, detail=True))


@bp.route("/maintenance-orders/<int:oid>/invoices", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def invoices_for_order(api_user, oid):
    """The MO screen's Invoices section.

    Without it a user cannot see what has already been recorded, and
    would enter the same invoice twice -- which then doubles the actual
    cost the order is completed against.
    """
    from app.modules.transactions.maintenance_invoice.models import (
        MaintenanceInvoice)

    rows = (MaintenanceInvoice.query
            .filter_by(maintenance_order_id=oid)
            .order_by(MaintenanceInvoice.id.desc())
            .all())
    return jsonify({"items": [_invoice_json(i) for i in rows]})


@bp.route("/invoices/<int:iid>/lines", methods=["POST"])
@api_auth_required("maintenanceinvoice.update")
def add_invoice_line(api_user, iid):
    """Add a line. The service recalculates the header totals.

    expense_category and charged_to are REQUIRED, not defaulted:

      * expense_category drives the MO Cost Summary's category rows. A
        line without one lands in the total and under no row -- exactly
        the breakage the grouped summary was built to avoid.
      * charged_to decides who pays. An approver seeing INSURANCE_CLAIM
        rather than COMPANY is looking at a different decision, and
        defaulting it would silently charge the company for something
        claimable.
    """
    from app.modules.transactions.maintenance_invoice.service import (
        MaintenanceInvoiceService)

    payload = request.get_json(silent=True) or {}
    for required in ("part_description", "expense_category", "charged_to"):
        if not payload.get(required):
            return _bad(f"{required.replace('_', ' ').title()} is required.",
                        required)

    # quantity/unit_cost/discount are validated for shape here, even
    # though add_line() already casts them to Decimal internally --
    # that cast raises decimal.InvalidOperation on a bad string, which
    # is the CALLER's mistake, not a conflict with the invoice's state.
    # Left to the generic except-Exception handler below, it surfaced
    # as an opaque 409 ("[<class 'decimal.ConversionSyntax'>]") that
    # named no field and used the wrong status code for bad input.
    from decimal import Decimal, InvalidOperation
    for numeric_field, label in (("quantity", "Quantity"),
                                 ("unit_cost", "Unit Cost"),
                                 ("discount", "Discount")):
        if numeric_field not in payload:
            continue
        try:
            Decimal(str(payload[numeric_field]))
        except InvalidOperation:
            return _bad(f"{label} must be a number.", numeric_field)

    svc = MaintenanceInvoiceService()
    try:
        line = svc.add_line(
            iid,
            part_description=payload["part_description"],
            expense_category=payload["expense_category"],
            charged_to=payload["charged_to"],
            quantity=payload.get("quantity", 1),
            unit_cost=payload.get("unit_cost", 0),
            discount=payload.get("discount", 0),
            part_number=payload.get("part_number"),
            specification=payload.get("specification"),
            uom=payload.get("uom"),
            sort_order=payload.get("sort_order"))
    except Exception as exc:
        # Includes InvoiceLockedError: an APPROVED invoice refuses new
        # lines, which is a state conflict rather than bad input.
        return _conflict(str(exc))
    return jsonify(_line_json(line)), 201


@bp.route("/invoices/<int:iid>/lines/<int:line_id>", methods=["DELETE"])
@api_auth_required("maintenanceinvoice.update")
def remove_invoice_line(api_user, iid, line_id):
    from app.modules.transactions.maintenance_invoice.models import (
        MaintenanceInvoice)
    from app.modules.transactions.maintenance_invoice.service import (
        MaintenanceInvoiceService)

    from app.modules.transactions.maintenance_invoice.models import (
        MaintenanceInvoiceLine)

    # The service takes only the line id. Ownership is checked HERE so
    # /invoices/5/lines/99 cannot delete a line belonging to invoice 7 --
    # the URL asserts a relationship the service does not verify.
    line = MaintenanceInvoiceLine.query.filter_by(id=line_id).first()
    if line is None or line.invoice_id != iid:
        return _not_found("Invoice line")

    try:
        MaintenanceInvoiceService().remove_line(line_id)
    except Exception as exc:
        return _conflict(str(exc))
    inv = MaintenanceInvoice.query.filter_by(id=iid).first()
    return jsonify(_invoice_json(inv, detail=True))
