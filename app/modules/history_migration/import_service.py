"""Import service for the Vehicle History Migration template.

Reads two sheets -- Maintenance History and Registration History -- and
creates real MaintenanceOrder / VehicleRegistration rows from them,
tagged as historical so they never enter the live approval workflow or
appear in "For My Action".

Design decisions worth stating plainly:

  * Historical rows are created directly as COMPLETED with no
    approval_instance. The work already happened, sometimes years ago
    -- routing it through Draft -> Submitted -> Approved would be
    fiction, and would pollute this period's real approval statistics.

  * document_number is left NULL for historical rows rather than
    consuming a real number from the live numbering counter. Running a
    bulk historical import through AutoNumberingService would either
    hand out today's sequence to a 2019 record (nonsensical) or require
    rolling the counter back per period (fragile, and risks exactly the
    kind of numbering collision already fixed elsewhere in this
    project). A historical row's identity is its vehicle + date, not a
    generated number.

  * Every row from one file shares one HistoricalImportBatch, so a
    wrong upload can be undone as a single action across BOTH
    Maintenance and Registration history at once -- the same guarantee
    already built for fuel statement imports, and for the same reason:
    a bulk historical migration is exactly the kind of one-shot,
    high-stakes operation where an undo matters most.

  * A row that fails validation is skipped and reported, not fatal to
    the whole file. On a few hundred hand-transcribed rows, one bad
    date must not block the other 299.
"""
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from app.extensions import db


def _norm(value):
    return str(value).strip() if value is not None else ""


def _to_date(value, field_name):
    if value is None or _norm(value) == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _norm(value)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{field_name}: '{text}' is not a recognisable date "
                    f"(use YYYY-MM-DD)")


def _to_int(value, field_name):
    if value is None or _norm(value) == "":
        return None
    try:
        return int(Decimal(str(value).replace(",", "")))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_name}: '{value}' is not a whole number")


def _to_decimal(value, field_name):
    if value is None or _norm(value) == "":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"{field_name}: '{value}' is not a valid amount")


def _find_vehicle(vehicles_by_key, plate_or_conduction):
    key = _norm(plate_or_conduction).upper()
    return vehicles_by_key.get(key)


def _load_vehicle_index():
    """{plate or conduction number, uppercased: Vehicle}."""
    from app.modules.master_data.vehicle.models import Vehicle
    index = {}
    for v in Vehicle.query.all():
        if v.plate_number:
            index[v.plate_number.strip().upper()] = v
        if v.conduction_number:
            index[v.conduction_number.strip().upper()] = v
    return index


def import_vehicle_history(file_stream, dry_run: bool = True,
                          filename: str = None, user_id: int = None) -> dict:
    """Import both sheets of the Vehicle History Migration template.

    Returns per-sheet stats and a combined error list. Nothing is
    written to the database when dry_run is True.
    """
    from app.modules.history_migration.models import HistoricalImportBatch
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)

    wb = load_workbook(file_stream, data_only=True)
    vehicles = _load_vehicle_index()

    stats = {
        "maintenance": {"total_rows": 0, "created": 0, "skipped": 0},
        "registration": {"total_rows": 0, "created": 0, "skipped": 0},
        "errors": [],
    }

    batch = None
    if not dry_run:
        batch = HistoricalImportBatch(
            filename=filename, imported_by=user_id,
            imported_at=datetime.now())
        db.session.add(batch)
        db.session.flush()

    # ── Maintenance History ─────────────────────────────────────────
    if "Maintenance History" in wb.sheetnames:
        ws = wb["Maintenance History"]
        for row_number, row in enumerate(
                ws.iter_rows(min_row=2, values_only=True), start=2):
            if row is None or all(_norm(v) == "" for v in row):
                continue
            if _norm(row[0]).startswith("^"):
                continue   # the shipped "these rows are examples" note
            (plate, service_date, mtype, description, odometer,
             cost, shop, reference) = (list(row) + [None] * 8)[:8]

            stats["maintenance"]["total_rows"] += 1
            errors = []

            vehicle = _find_vehicle(vehicles, plate)
            if vehicle is None:
                errors.append(f"no vehicle matches '{plate}'")

            try:
                parsed_date = _to_date(service_date, "Date of Service")
                if parsed_date is None:
                    errors.append("Date of Service is required")
            except ValueError as exc:
                errors.append(str(exc)); parsed_date = None

            mtype_norm = _norm(mtype).upper()
            if mtype_norm not in ("PREVENTIVE", "CORRECTIVE"):
                errors.append(
                    f"Maintenance Type must be PREVENTIVE or CORRECTIVE, "
                    f"got '{mtype}'")

            try:
                odometer_val = _to_int(odometer, "Odometer at Service")
            except ValueError as exc:
                errors.append(str(exc)); odometer_val = None
            try:
                cost_val = _to_decimal(cost, "Cost")
            except ValueError as exc:
                errors.append(str(exc)); cost_val = None

            if errors:
                stats["maintenance"]["skipped"] += 1
                stats["errors"].append({
                    "sheet": "Maintenance History", "row": row_number,
                    "identifier": plate or "(blank)", "problems": errors})
                continue

            if dry_run:
                stats["maintenance"]["created"] += 1
                continue

            order = MaintenanceOrder(
                vehicle_id=vehicle.id, order_category="MAINTENANCE",
                category=mtype_norm[:12],
                description=_norm(description) or None,
                odometer_at_service=odometer_val,
                scheduled_date=parsed_date, completed_date=parsed_date,
                assigned_mechanic=_norm(shop) or None,
                actual_cost=cost_val, status="COMPLETED",
                is_historical=True, historical_batch_id=batch.id)
            db.session.add(order)
            stats["maintenance"]["created"] += 1

    # ── Registration History ────────────────────────────────────────
    if "Registration History" in wb.sheetnames:
        ws = wb["Registration History"]
        for row_number, row in enumerate(
                ws.iter_rows(min_row=2, values_only=True), start=2):
            if row is None or all(_norm(v) == "" for v in row):
                continue
            if _norm(row[0]).startswith("^"):
                continue   # the shipped "these rows are examples" note
            (plate, reg_type, reg_date, expiry, or_no, cr_no,
             cost) = (list(row) + [None] * 7)[:7]

            stats["registration"]["total_rows"] += 1
            errors = []

            vehicle = _find_vehicle(vehicles, plate)
            if vehicle is None:
                errors.append(f"no vehicle matches '{plate}'")

            reg_type_norm = _norm(reg_type).upper()
            if reg_type_norm not in ("NEW", "RENEWAL"):
                errors.append(
                    f"Registration Type must be NEW or RENEWAL, got "
                    f"'{reg_type}'")

            try:
                parsed_reg_date = _to_date(reg_date, "Registration Date")
                if parsed_reg_date is None:
                    errors.append("Registration Date is required")
            except ValueError as exc:
                errors.append(str(exc)); parsed_reg_date = None
            try:
                parsed_expiry = _to_date(expiry, "Expiry Date")
            except ValueError as exc:
                errors.append(str(exc)); parsed_expiry = None
            try:
                cost_val = _to_decimal(cost, "Cost")
            except ValueError as exc:
                errors.append(str(exc)); cost_val = None

            if errors:
                stats["registration"]["skipped"] += 1
                stats["errors"].append({
                    "sheet": "Registration History", "row": row_number,
                    "identifier": plate or "(blank)", "problems": errors})
                continue

            if dry_run:
                stats["registration"]["created"] += 1
                continue

            reg = VehicleRegistration(
                vehicle_id=vehicle.id, registration_type=reg_type_norm,
                registration_date=parsed_reg_date, expiry_date=parsed_expiry,
                or_number=_norm(or_no) or None,
                cr_number=_norm(cr_no) or None,
                or_cr_cost=cost_val, status="COMPLETED",
                is_historical=True, historical_batch_id=batch.id)
            db.session.add(reg)
            stats["registration"]["created"] += 1

    if not dry_run:
        batch.maintenance_rows = stats["maintenance"]["created"]
        batch.registration_rows = stats["registration"]["created"]
        db.session.commit()
        stats["batch_id"] = batch.id
    else:
        stats["batch_id"] = None

    return stats


def delete_historical_batch(batch_id):
    """Undo one historical import -- removes exactly and only the rows
    that one file created, across both Maintenance and Registration
    history."""
    from app.modules.history_migration.models import HistoricalImportBatch
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)

    mo_rows = MaintenanceOrder.query.filter_by(
        historical_batch_id=batch_id).all()
    vr_rows = VehicleRegistration.query.filter_by(
        historical_batch_id=batch_id).all()
    count = len(mo_rows) + len(vr_rows)
    for row in mo_rows + vr_rows:
        db.session.delete(row)
    batch = db.session.get(HistoricalImportBatch, batch_id)
    if batch:
        db.session.delete(batch)
    db.session.commit()
    return count


def build_template() -> bytes:
    """Build the fill-in Excel template as bytes, for the download
    route -- generated fresh each time rather than a static file
    committed to the repo, matching the vehicle importer's own pattern,
    so any future edit to the template only needs to happen in one
    place (this function)."""
    from io import BytesIO
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    HEADER_FILL = PatternFill("solid", fgColor="1F3B4D")
    HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    EXAMPLE_FONT = Font(name="Arial", italic=True, color="6B7280", size=10)
    INPUT_FILL = PatternFill("solid", fgColor="FFFDE7")
    BODY_FONT = Font(name="Arial", size=10)
    NOTE_RED = Font(name="Arial", italic=True, bold=True, color="C00000",
                    size=10)
    THIN = Side(style="thin", color="D1D5DB")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    wb = openpyxl.Workbook()

    # ── Maintenance History ─────────────────────────────────────────
    ws = wb.active
    ws.title = "Maintenance History"
    mh_cols = [
        ("Plate or Conduction Number", 22,
         "REQUIRED. Must match a vehicle already in the system exactly."),
        ("Date of Service", 16, "REQUIRED. Format: YYYY-MM-DD."),
        ("Maintenance Type", 16,
         "REQUIRED. One of: PREVENTIVE, CORRECTIVE."),
        ("Work Description", 40, "Recommended. Plain description."),
        ("Odometer at Service (km)", 20,
         "Leave BLANK if not known -- do NOT guess."),
        ("Cost (PHP)", 16, "Leave BLANK if not known. Numbers only."),
        ("Shop / Vendor Name", 24, "Optional."),
        ("Reference / Receipt No.", 22, "Optional."),
    ]
    ws.append([c[0] for c in mh_cols])
    for i, (name, width, _n) in enumerate(mh_cols, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font = HEADER_FONT; cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = BORDER
        ws.column_dimensions[cell.column_letter].width = width
    ws.row_dimensions[1].height = 30

    example_rows = [
        ["SAMPLE-0001", "2023-04-15", "PREVENTIVE",
         "Oil and filter change, brake pad replacement", 45320, 3500,
         "Speedy Auto Center", "OR-10234"],
        ["SAMPLE-0001", "2023-11-02", "CORRECTIVE",
         "Replaced worn timing belt after unusual noise", 52100, 8200,
         "Speedy Auto Center", "OR-10891"],
        ["SAMPLE-0002", "2022-08-20", "PREVENTIVE", "Routine PM service",
         None, 2800, "Vehicle owner's own mechanic", None],
    ]
    start_row = 2
    for r_idx, row in enumerate(example_rows, start=start_row):
        for c_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=value)
            cell.font = EXAMPLE_FONT; cell.border = BORDER
            if c_idx == 5 and value is not None:
                cell.number_format = "#,##0"
            if c_idx == 6 and value is not None:
                cell.number_format = "#,##0.00"
    note_row = start_row + len(example_rows)
    ws.cell(row=note_row, column=1,
           value="^ These 3 rows are EXAMPLES ONLY -- delete them before "
                "entering your real data.").font = NOTE_RED
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row,
                   end_column=len(mh_cols))
    for r in range(note_row + 2, note_row + 302):
        for c in range(1, len(mh_cols) + 1):
            cell = ws.cell(row=r, column=c)
            cell.fill = INPUT_FILL; cell.font = BODY_FONT; cell.border = BORDER
            if c == 5: cell.number_format = "#,##0"
            if c == 6: cell.number_format = "#,##0.00"
    dv_type = DataValidation(
        type="list", formula1='"PREVENTIVE,CORRECTIVE"', allow_blank=False,
        showErrorMessage=True, errorTitle="Invalid Maintenance Type",
        error="Please choose PREVENTIVE or CORRECTIVE from the dropdown.")
    ws.add_data_validation(dv_type)
    dv_type.add(f"C{note_row+2}:C{note_row+301}")
    ws.freeze_panes = "A2"

    # ── Registration History ────────────────────────────────────────
    ws2 = wb.create_sheet("Registration History")
    rh_cols = [
        ("Plate or Conduction Number", 22,
         "REQUIRED. Must match a vehicle already in the system exactly."),
        ("Registration Type", 16, "REQUIRED. NEW or RENEWAL."),
        ("Registration Date", 16, "REQUIRED. Format: YYYY-MM-DD."),
        ("Expiry Date", 16, "Leave BLANK if not known -- do NOT guess."),
        ("OR Number", 20, "Optional."),
        ("CR Number", 20, "Optional."),
        ("Cost (PHP)", 16, "Leave BLANK if not known."),
    ]
    ws2.append([c[0] for c in rh_cols])
    for i, (name, width, _n) in enumerate(rh_cols, start=1):
        cell = ws2.cell(row=1, column=i)
        cell.font = HEADER_FONT; cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = BORDER
        ws2.column_dimensions[cell.column_letter].width = width
    ws2.row_dimensions[1].height = 30

    rh_examples = [
        ["SAMPLE-0001", "NEW", "2020-01-10", "2023-01-10", "OR-55021",
         "CR-88213", 6500],
        ["SAMPLE-0001", "RENEWAL", "2023-01-08", "2026-01-10", "OR-70044",
         "CR-88213", 7200],
        ["SAMPLE-0002", "RENEWAL", "2022-06-15", None, "OR-61230", None,
         6800],
    ]
    start_row2 = 2
    for r_idx, row in enumerate(rh_examples, start=start_row2):
        for c_idx, value in enumerate(row, start=1):
            cell = ws2.cell(row=r_idx, column=c_idx, value=value)
            cell.font = EXAMPLE_FONT; cell.border = BORDER
            if c_idx == 7 and value is not None:
                cell.number_format = "#,##0.00"
    note_row2 = start_row2 + len(rh_examples)
    ws2.cell(row=note_row2, column=1,
            value="^ These 3 rows are EXAMPLES ONLY -- delete them before "
                 "entering your real data.").font = NOTE_RED
    ws2.merge_cells(start_row=note_row2, start_column=1, end_row=note_row2,
                    end_column=len(rh_cols))
    for r in range(note_row2 + 2, note_row2 + 202):
        for c in range(1, len(rh_cols) + 1):
            cell = ws2.cell(row=r, column=c)
            cell.fill = INPUT_FILL; cell.font = BODY_FONT; cell.border = BORDER
            if c == 7: cell.number_format = "#,##0.00"
    dv_regtype = DataValidation(
        type="list", formula1='"NEW,RENEWAL"', allow_blank=False,
        showErrorMessage=True, errorTitle="Invalid Registration Type",
        error="Please choose NEW or RENEWAL from the dropdown.")
    ws2.add_data_validation(dv_regtype)
    dv_regtype.add(f"B{note_row2+2}:B{note_row2+201}")
    ws2.freeze_panes = "A2"

    # ── Instructions (inserted first) ───────────────────────────────
    ws0 = wb.create_sheet("Instructions", 0)
    ws0.sheet_view.showGridLines = False
    TITLE = Font(name="Arial", bold=True, size=16, color="1F3B4D")
    H2 = Font(name="Arial", bold=True, size=12, color="1F3B4D")
    BODY = Font(name="Arial", size=10.5)
    RED = Font(name="Arial", bold=True, size=10.5, color="C00000")
    ws0.column_dimensions["A"].width = 100
    ws0["A1"] = "Vehicle History Migration — Instructions"
    ws0["A1"].font = TITLE
    ws0.row_dimensions[1].height = 26
    lines = [
        ("", None),
        ("What this workbook is for", H2),
        ("This workbook is how your vehicle's PAST maintenance and "
         "registration records get entered into the Fleet Management "
         "System. There are two sheets to fill in:", BODY),
        ("  • Maintenance History — every past service, repair, or PM "
         "you have a record of, one row per service event.", BODY),
        ("  • Registration History — every past LTO registration or "
         "renewal, one row per registration event.", BODY),
        ("", None),
        ("Before you start", H2),
        ("  1. Each row needs the vehicle's Plate Number (or Conduction "
         "Number if it has no plate yet) EXACTLY as it appears in the "
         "system.", BODY),
        ("  2. Delete the 3 example rows on each sheet before entering "
         "your own data.", BODY),
        ("  3. One row = one event.", BODY),
        ("", None),
        ("The single most important rule", H2),
        ("If you don't know a value — leave that cell BLANK. Do not "
         "guess, estimate, or write 'N/A', '0', or a rough number.", RED),
        ("A blank cell tells the system 'this wasn't recorded.' A "
         "guessed number looks exactly like a real one once it's in "
         "the system, and would quietly throw off maintenance-due "
         "calculations and cost reports later.", BODY),
        ("", None),
        ("Maintenance Type — what counts as which", H2),
        ("  • PREVENTIVE — routine, scheduled service.", BODY),
        ("  • CORRECTIVE — a repair because something broke or wore "
         "out.", BODY),
        ("", None),
        ("Dates", H2),
        ("Use YYYY-MM-DD. If you only know the month and year, use the "
         "1st of that month and note it for whoever reviews the file.",
         BODY),
        ("", None),
        ("When you're done", H2),
        ("Save the file and send it back. It will be reviewed using a "
         "preview step before anything is added to the live system.",
         BODY),
    ]
    row = 2
    for text, font in lines:
        cell = ws0.cell(row=row, column=1, value=text if text else None)
        cell.font = font if font else BODY
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws0.row_dimensions[row].height = 18 if text else 8
        row += 1

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
