"""Vendor bulk import from a fill-in Excel workbook (B24).

Deliberately the same shape as the vehicle importer
(app/modules/master_data/vehicle/import_service.py), because the client
asked for "the same UI as the vehicle upload":

  * build_template()  -> the expected headers, one clearly-marked example
    row, and a Reference sheet with every column's rule plus this
    install's own Branch codes, so nobody fills it in by guessing.
  * import_vendors()  -> validates (and, unless dry_run, saves) a
    filled-in workbook, reporting per-row problems instead of failing the
    whole file on one bad cell.

Rules agreed with the client:
  * A code that already exists is SKIPPED and reported. An import never
    changes an existing vendor -- corrections go through the vendor form,
    where they are deliberate and audited one at a time.
  * A row that fails validation is skipped; the rest still import (a
    partial import is far more useful for a migration than an
    all-or-nothing failure on row 200).
  * dry_run (the default upstream) runs EVERY check and writes nothing,
    so the preview reports exactly what a real import would.

Lookup columns are matched by CODE (branch code), never by id -- an id
means nothing to whoever fills in the sheet.
"""
import re
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from app.extensions import db
from app.modules.master_data.vehicle.import_coercion import clean as _clean

VENDOR_TYPES = ("GOODS", "SERVICES", "BOTH")

# (column header, required?, human-readable note for the Reference sheet)
TEMPLATE_COLUMNS = [
    ("code", True, "Unique vendor code, e.g. VEN-001. A code already in the "
                   "system is skipped, never overwritten."),
    ("name", True, "Registered business name."),
    ("vendor_type", False, "GOODS, SERVICES or BOTH. Blank = GOODS."),
    ("tin", False, "Tax Identification Number, e.g. 123-456-789-000."),
    ("contact_person", False, "Primary contact."),
    ("phone", False, "Landline or mobile."),
    ("email", False, "e.g. service@vendor.ph"),
    ("address", False, "Street address."),
    ("city", False, "City / municipality."),
    ("branch_codes", False, "Branches this vendor serves, comma-separated "
                            "(see codes below). BLANK = serves all branches."),
]

REQUIRED_COLUMNS = [c for c, required, _ in TEMPLATE_COLUMNS if required]

#: The model columns each template column writes to. Lengths are read
#: from the model at import time, so a widened column needs no change here.
_TEXT_FIELDS = ("code", "name", "tin", "contact_person", "phone", "email",
                "address", "city")

# Deliberately loose: catches "not-an-email" and "a@b" typos without
# rejecting real-world addresses a strict RFC pattern would.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_EXAMPLE_CODE = "EXAMPLE-VEN-001"


def _column_lengths():
    from app.modules.master_data.vendor.models import Vendor
    out = {}
    for name in _TEXT_FIELDS:
        length = getattr(Vendor.__table__.columns[name].type, "length", None)
        if length:
            out[name] = length
    return out


def build_template() -> bytes:
    from app.modules.master_data.org.models import Branch

    wb = Workbook()
    ws = wb.active
    ws.title = "Vendors"

    headers = [c for c, _, _ in TEMPLATE_COLUMNS]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="1F3B4D")
    required_fill = PatternFill("solid", fgColor="7A1F1F")
    for idx, (name, required, _note) in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = required_fill if required else header_fill
        ws.column_dimensions[cell.column_letter].width = max(16, len(name) + 6)
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["H"].width = 32
    ws.column_dimensions["J"].width = 24

    ws.append([_EXAMPLE_CODE, "Example Motors Inc.", "SERVICES",
               "000-000-000-000", "Juan Dela Cruz", "0917 000 0000",
               "service@example.ph", "123 Example St.", "Muntinlupa", ""])
    for idx in range(1, len(headers) + 1):
        ws.cell(row=2, column=idx).font = Font(italic=True, color="888888")
    ws.cell(row=3, column=1).value = "^ Delete this example row before uploading."
    ws.cell(row=3, column=1).font = Font(italic=True, bold=True, color="C00000")
    ws.freeze_panes = "A2"

    ref = wb.create_sheet("Reference")
    ref.append(["Column", "Required", "Notes"])
    for c in ref[1]:
        c.font = Font(bold=True)
    lengths = _column_lengths()
    for name, required, note in TEMPLATE_COLUMNS:
        limit = f" Max {lengths[name]} characters." if name in lengths else ""
        ref.append([name, "YES" if required else "optional", note + limit])
    ref.column_dimensions["A"].width = 18
    ref.column_dimensions["B"].width = 12
    ref.column_dimensions["C"].width = 80

    ref.append([])
    ref.append(["Valid vendor_type values:"])
    ref.cell(ref.max_row, 1).font = Font(bold=True)
    for t in VENDOR_TYPES:
        ref.append([t])

    ref.append([])
    ref.append(["Valid Branch codes:"])
    ref.cell(ref.max_row, 1).font = Font(bold=True)
    for b in Branch.query.filter_by(is_active=True).order_by(Branch.code).all():
        ref.append([b.code, b.name])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_vendors(file_stream, dry_run: bool = True) -> dict:
    """Validate (and optionally save) a filled-in template.

    Returns {total_rows, created, skipped, errors: [{row, identifier,
    problems}]} -- the vehicle importer's shape, so the React page and
    its result table are shared.
    """
    from app.modules.master_data.org.models import Branch
    from app.modules.master_data.vendor.models import Vendor
    from app.modules.master_data.vendor.service import (
        DuplicateCodeError, VendorService)

    wb = load_workbook(file_stream, data_only=True)
    ws = wb["Vendors"] if "Vendors" in wb.sheetnames else wb[wb.sheetnames[0]]

    header_row = [(_clean(c.value) or "").lower() or None for c in ws[1]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header_row]
    if missing:
        raise ValueError(
            f"The uploaded file is missing required column(s): "
            f"{', '.join(missing)}. Please start from the downloaded "
            f"template.")
    idx = {name: i for i, name in enumerate(header_row) if name}

    branches = {b.code.upper(): b.id for b in Branch.query.all() if b.code}
    existing = {c.upper() for (c,) in db.session.query(Vendor.code).all() if c}
    lengths = _column_lengths()
    seen_in_file = set()

    stats = {"total_rows": 0, "created": 0, "skipped": 0, "errors": []}
    svc = VendorService()

    for row_number, row in enumerate(ws.iter_rows(min_row=2, values_only=True),
                                     start=2):
        if row is None or all(_clean(v) is None for v in row):
            continue
        get = lambda col: (_clean(row[idx[col]])
                           if col in idx and idx[col] < len(row) else None)

        code = get("code")
        # The template's own example row and its "delete me" note are
        # never data, even when someone forgets to delete them.
        if code == _EXAMPLE_CODE or (code or "").startswith("^ Delete"):
            continue

        stats["total_rows"] += 1
        errors = []
        name = get("name")
        identifier = code or name or "(blank)"

        if not code:
            errors.append("code is required")
        if not name:
            errors.append("name is required")

        values = {f: get(f) for f in _TEXT_FIELDS}
        for field, limit in lengths.items():
            if values[field] and len(values[field]) > limit:
                errors.append(f"{field} must be at most {limit} characters "
                              f"(has {len(values[field])})")

        if code:
            # In-file repeat first: on a real import the first copy has
            # just been saved, so checking `existing` first would call the
            # repeat "already exists" -- true, but not the useful answer.
            key = code.upper()
            if key in seen_in_file:
                errors.append(f"code '{code}' appears more than once in "
                              f"this file -- only the first row is used")
            elif key in existing:
                errors.append(f"code '{code}' already exists -- skipped, "
                              f"the existing vendor was not changed")
            seen_in_file.add(key)

        vendor_type = (get("vendor_type") or "GOODS").upper()
        if vendor_type not in VENDOR_TYPES:
            errors.append(f"vendor_type '{vendor_type}' must be one of "
                          f"{', '.join(VENDOR_TYPES)}")

        email = values["email"]
        if email and not _EMAIL.match(email):
            errors.append(f"email '{email}' is not a valid email address")

        branch_ids = []
        for raw in (get("branch_codes") or "").replace(";", ",").split(","):
            bcode = raw.strip().upper()
            if not bcode:
                continue
            if bcode not in branches:
                errors.append(f"unknown branch code '{bcode}'")
            elif branches[bcode] not in branch_ids:
                branch_ids.append(branches[bcode])

        if errors:
            stats["skipped"] += 1
            stats["errors"].append({"row": row_number,
                                    "identifier": identifier,
                                    "problems": errors})
            continue

        if dry_run:
            stats["created"] += 1
            continue

        try:
            optional = {f: values[f] for f in _TEXT_FIELDS
                        if f not in ("code", "name") and values[f]}
            vendor = svc.create(code=code, name=name,
                                vendor_type=vendor_type, **optional)
            if branch_ids:
                svc.assign_branches(vendor.id, branch_ids)
            existing.add(code.upper())
            stats["created"] += 1
        except DuplicateCodeError as exc:   # raced with another save
            db.session.rollback()
            stats["skipped"] += 1
            stats["errors"].append({"row": row_number, "identifier": code,
                                    "problems": [str(exc)]})
        except Exception as exc:  # one bad row must not kill the batch
            db.session.rollback()
            stats["skipped"] += 1
            stats["errors"].append({"row": row_number, "identifier": code,
                                    "problems": [f"unexpected error: {exc}"]})

    return stats
