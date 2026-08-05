"""Fuel transaction import and export.

Deliberately provider-agnostic. The client's Petron statement format
isn't confirmed yet, so this reads by COLUMN HEADER through an alias
table rather than by fixed position. Adding Petron's exact headers later
is a few entries in COLUMN_ALIASES, not a rewrite — and a statement from
Shell or Caltex would work the same way.

Everything imported still passes through OdometerValidationService, so a
mistyped pump reading is quarantined on the way in rather than being
discovered later in a consumption report.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from sqlalchemy import func

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from app.extensions import db


# Header aliases, lower-cased and stripped. Providers name the same
# column half a dozen ways; matching on a set of known spellings means a
# new statement layout usually needs no code change at all.
COLUMN_ALIASES = {
    "transaction_date": ("transaction_date", "date", "txn date",
                        "transaction date", "posting date", "trans date",
                        "datetime", "date/time"),
    "card_number": ("card_number", "card no", "card no.", "card number",
                   "fleet card", "cardno", "card"),
    "plate_number": ("plate_number", "plate", "plate no", "plate no.",
                    "vehicle", "vehicle no", "conduction", "unit"),
    "station": ("station", "site", "site name", "station name",
               "merchant", "location", "branch"),
    "litres": ("litres", "liters", "volume", "qty", "quantity",
              "liters sold", "volume l"),
    "price_per_litre": ("price_per_litre", "unit price", "price l",
                       "price per liter", "price", "unit cost"),
    "total_amount": ("total_amount", "amount", "total", "gross amount",
                    "net amount", "total cost"),
    "odometer_reported": ("odometer_reported", "odometer", "odo",
                         "mileage", "km reading", "odometer reading",
                         "odometer reported"),
    "fuel_type": ("fuel_type", "product", "fuel", "product name",
                 "description", "fuel type"),
    "reference_number": ("reference_number", "reference", "ref no",
                        "ref no.", "receipt", "receipt no", "invoice no",
                        "transaction no", "or no"),
    "driver_name": ("driver_name", "driver", "driver name", "cardholder"),
}

TEMPLATE_COLUMNS = [
    ("transaction_date", True, "YYYY-MM-DD or YYYY-MM-DD HH:MM"),
    ("plate_number", True, "Plate or Conduction number — must match a vehicle."),
    ("card_number", False, "Fleet card number, if the fill was on a card."),
    ("station", False, "Station or site name."),
    ("fuel_type", False, "e.g. DIESEL, GASOLINE"),
    ("litres", True, "Numbers only, e.g. 45.5"),
    ("price_per_litre", False, "Numbers only, e.g. 62.15"),
    ("total_amount", True, "Numbers only, e.g. 2827.83"),
    ("odometer_reported", False,
     "Odometer at the pump. Leave BLANK if not captured — do not guess. "
     "A reading implying more than ~3,000 km since this vehicle's last "
     "fill will be flagged for review rather than accepted automatically "
     "— check the date and the reading are both correct for large gaps "
     "between fills."),
    ("reference_number", False,
     "Receipt / OR number. Used to avoid double-counting a re-imported "
     "statement."),
    ("driver_name", False, "For reference only."),
]


import re as _re


def _norm(value):
    """Lowercase, strip, and collapse punctuation to spaces.

    The exact-match approach this replaces failed on real, ordinary
    headers -- including this app's OWN export column names, like
    "Odometer (reported)" and "Price/L" -- because neither the
    parentheses nor the slash were stripped before comparing against a
    plain-text alias. A natural round-trip (export a file, edit it,
    re-import it) or a real supplier statement with slightly different
    punctuation would otherwise silently map nothing and every reading
    would come in as MISSING, which is exactly what happened importing
    a real sample file before this fix.
    """
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = _re.sub(r"[^a-z0-9]+", " ", text)
    return _re.sub(r"\s+", " ", text).strip()


def map_headers(header_row):
    """Map a statement's own headers onto our field names.

    Returns {field_name: column_index}. Unrecognised columns are simply
    ignored rather than failing the import — a provider statement
    carries plenty of columns we have no use for, and refusing the file
    over them would be obstructive.

    Both the header text and the alias list are run through the same
    _norm(), so punctuation on either side (a header's parentheses, an
    alias's own period or slash) can't cause a false mismatch.
    """
    normalized_aliases = {
        field: {_norm(a) for a in aliases}
        for field, aliases in COLUMN_ALIASES.items()
    }
    mapping = {}
    for index, raw in enumerate(header_row):
        text = _norm(raw)
        if not text:
            continue
        for field, aliases in normalized_aliases.items():
            if field in mapping:
                continue
            if text in aliases:
                mapping[field] = index
                break
    return mapping


def build_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Fuel"
    ws.append([c for c, _, _ in TEMPLATE_COLUMNS])
    fill = PatternFill("solid", fgColor="1F3B4D")
    for i, (name, _r, _n) in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        ws.column_dimensions[cell.column_letter].width = max(16, len(name) + 4)

    ws.append(["2026-01-15 09:30", "ABC-1234", "7001234567890", "Petron EDSA",
              "DIESEL", 45.5, 62.15, 2827.83, 65430, "OR-100234", "Juan Cruz"])
    for i in range(1, len(TEMPLATE_COLUMNS) + 1):
        ws.cell(row=2, column=i).font = Font(italic=True, color="888888")
    ws.cell(row=3, column=1).value = "^ Delete this example row before uploading."
    ws.cell(row=3, column=1).font = Font(italic=True, bold=True, color="C00000")

    ref = wb.create_sheet("Reference")
    ref.append(["Column", "Required", "Notes"])
    for c in ref[1]:
        c.font = Font(bold=True)
    for name, required, note in TEMPLATE_COLUMNS:
        ref.append([name, "YES" if required else "optional", note])
    ref.column_dimensions["A"].width = 24
    ref.column_dimensions["C"].width = 70
    ref.append([])
    ref.append(["A provider statement can be uploaded directly — columns are "
               "matched by name, so exact ordering does not matter."])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _to_decimal(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("₱", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"'{value}' is not a valid number")


def _to_int(value):
    d = _to_decimal(value)
    return int(d) if d is not None else None


def _to_datetime(value):
    if value is None or str(value).strip() == "":
        raise ValueError("a transaction date is required")
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",   # <input type="datetime-local">
               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
               "%m/%d/%Y %H:%M", "%m/%d/%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y",
               "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a recognisable date")


def import_fuel(file_stream, dry_run: bool = True, filename: str = None,
                user_id: int = None) -> dict:
    """Load a fuel statement. Per-row errors, partial import.

    A row that fails is SKIPPED and reported; the rest still import. On a
    200-row monthly statement, an all-or-nothing failure over one bad
    cell would be far worse than a partial load with a list to fix.

    Every row created is stamped with the same import_batch_id, the
    original filename, who imported it, and when -- so a wrong upload
    can be found and undone as a whole rather than row by row.
    """
    import uuid
    from datetime import datetime as _dt
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.transactions.fuel.models import (
        FuelCard, FuelTransaction)
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService

    batch_id = uuid.uuid4().hex[:12]
    imported_at = _dt.now()

    wb = load_workbook(file_stream, data_only=True)
    ws = wb["Fuel"] if "Fuel" in wb.sheetnames else wb[wb.sheetnames[0]]
    header = [c.value for c in ws[1]]
    mapping = map_headers(header)

    missing = [f for f in ("transaction_date", "litres", "total_amount")
              if f not in mapping]
    if missing:
        raise ValueError(
            "Could not find these column(s) in the file: "
            + ", ".join(missing)
            + ". Columns are matched by name — check the header row, or "
              "start from the downloaded template.")
    if "plate_number" not in mapping and "card_number" not in mapping:
        raise ValueError(
            "The file needs either a plate/vehicle column or a card number "
            "column, so each fill can be matched to a vehicle.")

    vehicles = {}
    for v in Vehicle.query.all():
        for key in (v.plate_number, v.conduction_number):
            if key:
                vehicles[_norm(key)] = v
    cards = {_norm(c.card_number): c for c in FuelCard.query.all()
            if c.card_number}

    stats = {"total_rows": 0, "created": 0, "skipped": 0, "errors": []}
    validator = OdometerValidationService()
    analytics = FuelAnalyticsService()
    created_rows = []

    for row_number, row in enumerate(ws.iter_rows(min_row=2,
                                                 values_only=True), start=2):
        if row is None or all(_norm(v) == "" for v in row):
            continue

        def get(field):
            index = mapping.get(field)
            if index is None or index >= len(row):
                return None
            return row[index]

        plate = _norm(get("plate_number"))
        if plate == "abc-1234":
            continue                     # the template's example row

        stats["total_rows"] += 1
        errors = []

        vehicle = vehicles.get(plate) if plate else None
        card = cards.get(_norm(get("card_number")))
        if vehicle is None and card is not None:
            # A card assigned to a vehicle identifies the vehicle even
            # when the statement doesn't name one.
            vehicle = card.vehicle
        if vehicle is None:
            errors.append(
                f"no vehicle matches '{get('plate_number') or get('card_number')}'")

        try:
            when = _to_datetime(get("transaction_date"))
        except ValueError as exc:
            errors.append(f"transaction_date: {exc}")
            when = None

        try:
            litres = _to_decimal(get("litres"))
            if litres is None or litres <= 0:
                errors.append("litres must be greater than zero")
        except ValueError as exc:
            errors.append(f"litres: {exc}")
            litres = None

        try:
            amount = _to_decimal(get("total_amount"))
            if amount is None:
                errors.append("total_amount is required")
        except ValueError as exc:
            errors.append(f"total_amount: {exc}")
            amount = None

        try:
            price = _to_decimal(get("price_per_litre"))
        except ValueError:
            price = None                 # derived below rather than failing
        try:
            odometer = _to_int(get("odometer_reported"))
        except ValueError:
            # A junk odometer must NOT fail the row -- the fill still
            # happened and the money is real. It is imported without a
            # reading and shows as MISSING, which is exactly what the
            # validation layer is for.
            odometer = None

        reference = get("reference_number")
        reference = str(reference).strip() if reference else None

        if errors:
            stats["skipped"] += 1
            stats["errors"].append({
                "row": row_number,
                "identifier": get("plate_number") or get("card_number") or "(blank)",
                "problems": errors})
            continue

        if price is None and litres and amount:
            price = round(amount / litres, 4)

        duplicate = FuelTransaction.query.filter_by(
            vehicle_id=vehicle.id, transaction_date=when,
            reference_number=reference).first()
        if duplicate:
            stats["skipped"] += 1
            stats["errors"].append({
                "row": row_number, "identifier": vehicle.plate_number,
                "problems": ["already imported (same vehicle, date and "
                            "reference) — skipped to avoid double-counting"]})
            continue

        if dry_run:
            stats["created"] += 1
            continue

        txn = FuelTransaction(
            vehicle_id=vehicle.id, fuel_card_id=card.id if card else None,
            transaction_date=when,
            station=str(get("station")).strip() if get("station") else None,
            fuel_type=(str(get("fuel_type")).strip().upper()
                      if get("fuel_type") else None),
            litres=litres, price_per_litre=price, total_amount=amount,
            odometer_reported=odometer, reference_number=reference,
            source="IMPORT", import_filename=filename,
            import_batch_id=batch_id, imported_by=user_id,
            imported_at=imported_at)
        db.session.add(txn)
        db.session.flush()
        created_rows.append(txn)
        stats["created"] += 1

    if not dry_run and created_rows:
        # Validate oldest-first per vehicle, so each fill measures from a
        # predecessor that has already been assessed. Validating in file
        # order would compare against readings not yet checked.
        for vehicle_id in {t.vehicle_id for t in created_rows}:
            validator.revalidate_vehicle(vehicle_id)
        for txn in created_rows:
            analytics.detect_anomalies(txn, commit=False)
        db.session.commit()

    stats["flagged"] = sum(1 for t in created_rows if t.anomaly_flags)
    stats["untrusted_odometer"] = sum(
        1 for t in created_rows
        if t.odometer_status in ("SUSPECT", "MISSING"))
    stats["import_batch_id"] = batch_id if not dry_run and created_rows else None
    stats["filename"] = filename
    return stats


def list_import_batches(limit=30):
    """Recent import batches, most recent first, for the batch filter
    dropdown and the "undo a bad upload" screen."""
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.user_management.models import User

    rows = (db.session.query(
               FuelTransaction.import_batch_id,
               FuelTransaction.import_filename,
               FuelTransaction.imported_by,
               FuelTransaction.imported_at,
               func.count(FuelTransaction.id).label("row_count"),
               func.sum(FuelTransaction.total_amount).label("total_spend"))
           .filter(FuelTransaction.import_batch_id.isnot(None))
           .group_by(FuelTransaction.import_batch_id,
                    FuelTransaction.import_filename,
                    FuelTransaction.imported_by,
                    FuelTransaction.imported_at)
           .order_by(FuelTransaction.imported_at.desc())
           .limit(limit).all())

    users = {u.id: u for u in User.query.all()}
    return [{
        "batch_id": r.import_batch_id, "filename": r.import_filename,
        "imported_by": users.get(r.imported_by),
        "imported_at": r.imported_at, "row_count": r.row_count,
        "total_spend": r.total_spend or 0,
    } for r in rows]


def delete_import_batch(batch_id):
    """Remove every transaction from one upload -- the undo for a wrong
    file. Deliberately keyed on the batch, not a date range or a vehicle
    list, so it removes exactly and only what one upload added, nothing
    from before it and nothing added since by a different upload."""
    from app.modules.transactions.fuel.models import FuelTransaction
    rows = FuelTransaction.query.filter_by(import_batch_id=batch_id).all()
    count = len(rows)
    affected_vehicles = {r.vehicle_id for r in rows}
    for row in rows:
        db.session.delete(row)
    db.session.commit()

    # Deleting a batch can remove the reading a later, still-present fill
    # was measuring FROM -- that later fill's distance/km-L would then be
    # silently wrong until recomputed against whatever baseline remains.
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    validator = OdometerValidationService()
    for vehicle_id in affected_vehicles:
        validator.revalidate_vehicle(vehicle_id)

    return count


def export_fuel(rows) -> bytes:
    """Export transactions, including the validation verdict.

    The odometer status and note travel with the data deliberately: a
    spreadsheet showing consumption without showing which rows were
    excluded from it would invite exactly the false confidence this
    module exists to prevent.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Fuel Transactions"
    headers = ["Date", "Vehicle", "Card", "Station", "Fuel Type", "Litres",
              "Price/L", "Total", "Odometer (reported)", "Odometer (used)",
              "Odometer Status", "Distance (km)", "km/L", "Cost/km",
              "Flags", "Note"]
    ws.append(headers)
    fill = PatternFill("solid", fgColor="1F3B4D")
    for i in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["P"].width = 60

    for t in rows:
        ws.append([
            t.transaction_date.strftime("%Y-%m-%d %H:%M") if t.transaction_date else "",
            (t.vehicle.plate_number or t.vehicle.conduction_number) if t.vehicle else "",
            t.fuel_card.card_number if t.fuel_card else "",
            t.station or "", t.fuel_type or "",
            float(t.litres) if t.litres is not None else "",
            float(t.price_per_litre) if t.price_per_litre is not None else "",
            float(t.total_amount) if t.total_amount is not None else "",
            t.odometer_reported if t.odometer_reported is not None else "",
            t.odometer_used if t.odometer_used is not None else "",
            t.odometer_status or "",
            t.distance_km if t.distance_km is not None else "",
            float(t.km_per_litre) if t.km_per_litre is not None else "",
            float(t.cost_per_km) if t.cost_per_km is not None else "",
            t.anomaly_flags or "", t.odometer_note or "",
        ])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
