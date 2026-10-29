"""Driver / Assignee bulk import from a fill-in Excel workbook.

Deliberately the same shape as the vendor and vehicle importers
(app/modules/master_data/vendor/import_service.py,
app/modules/master_data/vehicle/import_service.py) -- the client asked
to "proceed in the same process of vendor and vehicle upload" for
master-data creation week:

  * build_template()  -> the expected headers, one clearly-marked example
    row, and a Reference sheet with every column's rule plus this
    install's own Branch/Department codes and the valid lookup values,
    so nobody fills it in by guessing.
  * import_drivers()  -> validates (and, unless dry_run, saves) a
    filled-in workbook, reporting per-row problems instead of failing the
    whole file on one bad cell.

Rules agreed with the client (same as vendor/vehicle):
  * An employee_number or license_number that already exists is SKIPPED
    and reported. An import never changes an existing driver/assignee --
    corrections go through the Driver/Assignee form, where they are
    deliberate and audited one at a time.
  * A row that fails validation is skipped; the rest still import.
  * dry_run (the default upstream) runs EVERY check and writes nothing.

Lookup columns (branch_code, department_code, assignee_type, status) are
matched by CODE, never by id.

**Photo is deliberately NOT a template column.** DriverService.create()
requires a photo file for every new record while System Parameter
REQUIRE_ASSIGNEE_PHOTO is at its default (True) -- and a bulk Excel
upload has no way to carry a photo per row. This importer calls
DriverService.create() exactly as /api/v1/drivers POST's own JSON path
already does for this same situation (see that route's docstring: "JSON
stays supported because the migration importer has no photo to send"),
so the established, pre-existing design is: turn REQUIRE_ASSIGNEE_PHOTO
(and, if the client's roster has no licenses on file yet,
REQUIRE_DRIVER_LICENSE) OFF in System Administration for the duration of
the bulk upload, then back ON afterward. The Driver/Assignee list's
`has_photo` column (see app/modules/api/drivers.py) exists for exactly
this follow-up: finding every imported record that still needs a photo
attached one at a time via Edit Driver. The Reference sheet below says
this plainly so it is not a surprise mid-import.
"""
import re
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from app.extensions import db
from app.modules.master_data.vehicle.import_coercion import (
    CoercionError, clean as _clean, coerce_date)

ASSIGNEE_TYPES = ("DRIVER", "EMPLOYEE", "THIRD_PARTY_DELIVERY", "CONSULTANT")
STATUSES = ("ACTIVE", "INACTIVE", "SUSPENDED")

# (column header, required?, human-readable note for the Reference sheet)
TEMPLATE_COLUMNS = [
    ("employee_number", True, "Unique employee number, e.g. EMP-0001. An "
                              "employee number already in the system is "
                              "skipped, never overwritten."),
    ("first_name", True, "Given name."),
    ("last_name", True, "Surname."),
    ("middle_name", False, "Shown as a middle initial on printed forms."),
    ("suffix", False, "e.g. Jr., Sr., III."),
    ("nickname", False, ""),
    ("assignee_type", False, "DRIVER, EMPLOYEE, THIRD_PARTY_DELIVERY or "
                             "CONSULTANT. Blank = DRIVER."),
    ("branch_code", True, "See Branch codes below."),
    ("department_code", False, "See Department codes below. Looked up "
                               "within the row's own Branch."),
    ("section", False, ""),
    ("position", False, ""),
    ("job_title", False, "Used on the PM Work Order report's Assignee "
                         "Position field; kept separate from Position."),
    ("cost_center", False, ""),
    ("employment_status", False, ""),
    ("employment_type", False, ""),
    ("license_number", False, "Required when assignee_type = DRIVER, "
                              "unless System Parameter "
                              "REQUIRE_DRIVER_LICENSE has been switched "
                              "off for this import."),
    ("license_expiry", False, "Date, e.g. 2027-05-15. Same requirement as "
                              "license_number."),
    ("license_type", False, "Same requirement as license_number."),
    ("phone", False, "Mobile number."),
    ("office_number", False, ""),
    ("home_number", False, ""),
    ("email", False, "e.g. juan.delacruz@client.ph"),
    ("complete_address", False, ""),
    ("emergency_contact_person", False, ""),
    ("emergency_contact_number", False, ""),
    ("business_name", False, "For THIRD_PARTY_DELIVERY / CONSULTANT "
                             "assignees representing an outside business."),
    ("business_address", False, ""),
    ("business_contact_no", False, ""),
    ("status", False, "ACTIVE, INACTIVE or SUSPENDED. Blank = ACTIVE."),
]

REQUIRED_COLUMNS = [c for c, required, _ in TEMPLATE_COLUMNS if required]

#: The model columns each plain-text template column writes to straight
#: through. Lengths are read from the model at import time, so a widened
#: column needs no change here.
_TEXT_FIELDS = (
    "employee_number", "first_name", "last_name", "middle_name", "suffix",
    "nickname", "section", "position", "job_title", "cost_center",
    "employment_status", "employment_type", "license_number",
    "license_type", "phone", "office_number", "home_number", "email",
    "complete_address", "emergency_contact_person",
    "emergency_contact_number", "business_name", "business_address",
    "business_contact_no",
)

# Deliberately loose: catches "not-an-email" and "a@b" typos without
# rejecting real-world addresses a strict RFC pattern would.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_EXAMPLE_EMPLOYEE_NUMBER = "EXAMPLE-0001"


def _column_lengths():
    from app.modules.master_data.driver.models import Driver
    out = {}
    for name in _TEXT_FIELDS:
        length = getattr(Driver.__table__.columns[name].type, "length", None)
        if length:
            out[name] = length
    return out


def build_template() -> bytes:
    from app.modules.master_data.org.models import Branch, Department

    wb = Workbook()
    ws = wb.active
    ws.title = "Drivers"

    headers = [c for c, _, _ in TEMPLATE_COLUMNS]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="1F3B4D")
    required_fill = PatternFill("solid", fgColor="7A1F1F")
    for idx, (name, required, _note) in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = required_fill if required else header_fill
        ws.column_dimensions[cell.column_letter].width = max(16, len(name) + 6)

    example = {
        "employee_number": _EXAMPLE_EMPLOYEE_NUMBER, "first_name": "Juan",
        "last_name": "Dela Cruz", "middle_name": "Santos", "suffix": "",
        "nickname": "Johnny", "assignee_type": "DRIVER",
        "branch_code": "", "department_code": "", "section": "Logistics",
        "position": "Driver", "job_title": "Company Driver",
        "cost_center": "", "employment_status": "REGULAR",
        "employment_type": "FULL_TIME", "license_number": "N01-23-456789",
        "license_expiry": "2027-05-15", "license_type": "PROFESSIONAL",
        "phone": "0917 000 0000", "office_number": "", "home_number": "",
        "email": "juan.delacruz@example.ph",
        "complete_address": "123 Example St., Muntinlupa",
        "emergency_contact_person": "Maria Dela Cruz",
        "emergency_contact_number": "0918 000 0000", "business_name": "",
        "business_address": "", "business_contact_no": "", "status": "ACTIVE",
    }
    first_branch = Branch.query.filter_by(is_active=True).order_by(
        Branch.code).first()
    example["branch_code"] = first_branch.code if first_branch else "BR-001"
    ws.append([example.get(h, "") for h in headers])
    for idx in range(1, len(headers) + 1):
        ws.cell(row=2, column=idx).font = Font(italic=True, color="888888")
    ws.cell(row=3, column=1).value = (
        "^ Delete this example row before uploading.")
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
    ref.column_dimensions["A"].width = 24
    ref.column_dimensions["B"].width = 12
    ref.column_dimensions["C"].width = 90

    ref.append([])
    ref.append(["Photos are not uploaded here -- see the module notes "
               "(ask your implementer) for how to add each person's "
               "photo after this import, or how to temporarily switch "
               "off the photo requirement for this batch."])
    ref.cell(ref.max_row, 1).font = Font(italic=True, bold=True)

    ref.append([])
    ref.append(["Valid assignee_type values:"])
    ref.cell(ref.max_row, 1).font = Font(bold=True)
    for t in ASSIGNEE_TYPES:
        ref.append([t])

    ref.append([])
    ref.append(["Valid status values:"])
    ref.cell(ref.max_row, 1).font = Font(bold=True)
    for s in STATUSES:
        ref.append([s])

    ref.append([])
    ref.append(["Valid Branch codes:"])
    ref.cell(ref.max_row, 1).font = Font(bold=True)
    for b in Branch.query.filter_by(is_active=True).order_by(Branch.code).all():
        ref.append([b.code, b.name])

    ref.append([])
    ref.append(["Valid Department codes (code is unique per Branch):"])
    ref.cell(ref.max_row, 1).font = Font(bold=True)
    for d in Department.query.filter_by(is_active=True).order_by(
            Department.code).all():
        ref.append([d.code, d.name,
                   d.branch.code if d.branch else ""])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_drivers(file_stream, dry_run: bool = True) -> dict:
    """Validate (and optionally save) a filled-in template.

    Returns {total_rows, created, skipped, errors: [{row, identifier,
    problems}]} -- the vendor/vehicle importers' shape, so the React
    page and its result table are shared.
    """
    from app.modules.master_data.driver.models import Driver
    from app.modules.master_data.driver.service import (
        DriverService, DuplicateDriverError, InvalidAssigneeError)
    from app.modules.master_data.org.models import Branch, Department

    wb = load_workbook(file_stream, data_only=True)
    ws = wb["Drivers"] if "Drivers" in wb.sheetnames else wb[wb.sheetnames[0]]

    header_row = [(_clean(c.value) or "").lower() or None for c in ws[1]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header_row]
    if missing:
        raise ValueError(
            f"The uploaded file is missing required column(s): "
            f"{', '.join(missing)}. Please start from the downloaded "
            f"template.")
    idx = {name: i for i, name in enumerate(header_row) if name}

    branches = {b.code.upper(): b for b in Branch.query.all() if b.code}
    departments_by_branch = {}
    for d in Department.query.all():
        if d.code:
            departments_by_branch.setdefault(d.branch_id, {})[
                d.code.upper()] = d.id
    existing_employee_numbers = {
        e.upper() for (e,) in
        db.session.query(Driver.employee_number).all() if e}
    existing_license_numbers = {
        l.upper() for (l,) in
        db.session.query(Driver.license_number).all() if l}
    lengths = _column_lengths()
    seen_employee_numbers = set()
    seen_license_numbers = set()

    stats = {"total_rows": 0, "created": 0, "skipped": 0, "errors": []}
    svc = DriverService()

    for row_number, row in enumerate(ws.iter_rows(min_row=2, values_only=True),
                                     start=2):
        if row is None or all(_clean(v) is None for v in row):
            continue
        get = lambda col: (_clean(row[idx[col]])
                           if col in idx and idx[col] < len(row) else None)

        employee_number = get("employee_number")
        # The template's own example row and its "delete me" note are
        # never data, even when someone forgets to delete them.
        if (employee_number == _EXAMPLE_EMPLOYEE_NUMBER
                or (employee_number or "").startswith("^ Delete")):
            continue

        stats["total_rows"] += 1
        errors = []
        first_name = get("first_name")
        last_name = get("last_name")
        identifier = (employee_number or
                     f"{first_name or ''} {last_name or ''}".strip() or
                     "(blank)")

        if not employee_number:
            errors.append("employee_number is required")
        if not first_name:
            errors.append("first_name is required")
        if not last_name:
            errors.append("last_name is required")

        values = {f: get(f) for f in _TEXT_FIELDS}
        for field, limit in lengths.items():
            if values[field] and len(values[field]) > limit:
                errors.append(f"{field} must be at most {limit} characters "
                              f"(has {len(values[field])})")

        if employee_number:
            key = employee_number.upper()
            if key in seen_employee_numbers:
                errors.append(
                    f"employee_number '{employee_number}' appears more "
                    f"than once in this file -- only the first row is used")
            elif key in existing_employee_numbers:
                errors.append(
                    f"employee_number '{employee_number}' already exists "
                    f"-- skipped, the existing record was not changed")
            seen_employee_numbers.add(key)

        license_number = values["license_number"]
        if license_number:
            lkey = license_number.upper()
            if lkey in seen_license_numbers:
                errors.append(
                    f"license_number '{license_number}' appears more "
                    f"than once in this file -- only the first row is used")
            elif lkey in existing_license_numbers:
                errors.append(
                    f"license_number '{license_number}' already exists "
                    f"-- skipped, the existing record was not changed")
            seen_license_numbers.add(lkey)

        assignee_type = (get("assignee_type") or "DRIVER").upper()
        if assignee_type not in ASSIGNEE_TYPES:
            errors.append(f"assignee_type '{assignee_type}' must be one of "
                          f"{', '.join(ASSIGNEE_TYPES)}")

        status = (get("status") or "ACTIVE").upper()
        if status not in STATUSES:
            errors.append(f"status '{status}' must be one of "
                          f"{', '.join(STATUSES)}")

        email = values["email"]
        if email and not _EMAIL.match(email):
            errors.append(f"email '{email}' is not a valid email address")

        branch_code = get("branch_code")
        branch = None
        if not branch_code:
            errors.append("branch_code is required")
        else:
            branch = branches.get(branch_code.upper())
            if branch is None:
                errors.append(f"unknown branch code '{branch_code}'")

        department_id = None
        department_code = get("department_code")
        if department_code:
            if branch is not None:
                department_id = departments_by_branch.get(
                    branch.id, {}).get(department_code.upper())
                if department_id is None:
                    errors.append(
                        f"unknown department code '{department_code}' for "
                        f"branch '{branch_code}'")
            else:
                errors.append(
                    "department_code given without a resolvable "
                    "branch_code")

        license_expiry = None
        try:
            license_expiry = coerce_date(get("license_expiry"),
                                         "license_expiry")
        except CoercionError as exc:
            errors.append(str(exc))

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
                        if f not in ("employee_number", "first_name",
                                    "last_name", "license_number",
                                    "license_type") and values[f]}
            optional["status"] = status
            if department_id:
                optional["department_id"] = department_id
            driver = svc.create(
                employee_number=employee_number, first_name=first_name,
                last_name=last_name, branch_id=branch.id,
                assignee_type=assignee_type,
                license_number=license_number,
                license_expiry=license_expiry,
                license_type=values["license_type"],
                photo_file=None, **optional)
            existing_employee_numbers.add(employee_number.upper())
            if license_number:
                existing_license_numbers.add(license_number.upper())
            stats["created"] += 1
        except (DuplicateDriverError, InvalidAssigneeError) as exc:
            db.session.rollback()
            stats["skipped"] += 1
            stats["errors"].append({"row": row_number,
                                    "identifier": identifier,
                                    "problems": [str(exc)]})
        except Exception as exc:  # one bad row must not kill the batch
            db.session.rollback()
            stats["skipped"] += 1
            stats["errors"].append({"row": row_number,
                                    "identifier": identifier,
                                    "problems": [f"unexpected error: {exc}"]})

    return stats
