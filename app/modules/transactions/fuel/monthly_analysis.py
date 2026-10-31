"""Fuel Analysis report -- the monthly per-vehicle engine.

Rebuilds the client's own MONTHLY FUEL ANALYSIS 2026.xlsx
("2026 FUEL COST & CONSUMPTION") layout and intent, but NOT its
formulas -- those were inspected first (Stage 1 impact analysis) and
three genuine defects were found and deliberately not carried over:

  * Some months chained their KM from a fixed baseline column instead
    of the previous month's own reading.
  * IFERROR(..., 0) showed 0 for data that was actually missing, not
    zero -- indistinguishable from "no consumption" in a fleet report.
  * A blank future month computed as a NEGATIVE distance.

This module fixes all three by chaining End Odometer across whatever
gap actually exists (skipping months with no activity or no
trustworthy reading) and by returning an explicit `reason` instead of
a number whenever a figure genuinely isn't computable.

"End Odometer" for a month is the reading from that month's LATEST
transaction (by date, not by value) among readings the existing
OdometerValidationService already trusts (odometer_status OK or
CORRECTED) -- a SUSPECT or MISSING reading never becomes "the" end
odometer for its month. Litres and cost are totalled for every
transaction in the month regardless of odometer trustworthiness: the
fuel was genuinely bought either way, and showing it only when the
odometer also happened to be readable would quietly understate real
spend.
"""
from calendar import monthrange
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

TRUSTED_ODOMETER_STATUSES = ("OK", "CORRECTED")

MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class FuelMonthlyAnalysisService:

    def _filtered_vehicle_query(self, *, branch_id=None, department_id=None,
                                vehicle_type_id=None, plate=None, user=None):
        from app.modules.master_data.vehicle.models import Vehicle
        from app.modules.transactions.fuel.analytics import fuel_scope_ids

        q = Vehicle.query.filter(Vehicle.is_active.is_(True),
                                Vehicle.status != "DISPOSED")
        scope_ids = fuel_scope_ids(user, branch_id)
        if scope_ids is not None:
            q = q.filter(Vehicle.branch_id.in_(scope_ids))
        if department_id:
            q = q.filter(Vehicle.department_id == department_id)
        if vehicle_type_id:
            q = q.filter(Vehicle.vehicle_type_id == vehicle_type_id)
        if plate:
            needle = f"%{plate.strip().lower()}%"
            from sqlalchemy import func, or_
            q = q.filter(or_(
                func.lower(Vehicle.plate_number).like(needle),
                func.lower(Vehicle.conduction_number).like(needle)))
        return q

    def _vehicle_transactions(self, vehicle_id, *, fuel_type=None,
                              station=None, through_year):
        """Every transaction for this vehicle up to and including the
        report year, oldest first -- a prior-year reading is needed to
        establish January's baseline, and a later year's data must
        never leak into this report."""
        from app.modules.transactions.fuel.models import FuelTransaction
        q = FuelTransaction.query.filter(
            FuelTransaction.vehicle_id == vehicle_id,
            FuelTransaction.transaction_date
            < datetime(through_year + 1, 1, 1))
        if fuel_type:
            q = q.filter(FuelTransaction.fuel_type == fuel_type)
        if station:
            from sqlalchemy import func
            q = q.filter(func.lower(FuelTransaction.station)
                        == station.strip().lower())
        return q.order_by(FuelTransaction.transaction_date.asc()).all()

    def _vehicle_series(self, vehicle_id, year, *, fuel_type=None,
                        station=None):
        """The 12-month series for one vehicle.

        The running baseline is re-derived each month from every
        TRUSTED reading strictly before that month's start, rather
        than carried forward by hand -- so a gap of any length (no
        activity, an untrusted reading, several such months in a row)
        is bridged automatically to whichever earlier month last had
        one, with nothing to get out of sync.
        """
        txns = self._vehicle_transactions(
            vehicle_id, fuel_type=fuel_type, station=station,
            through_year=year)

        months = []
        for month in range(1, 13):
            start = datetime(year, month, 1)
            end = datetime(year, month, monthrange(year, month)[1], 23, 59, 59)
            month_txns = [t for t in txns if start <= t.transaction_date <= end]
            prior_trusted = [t for t in txns
                            if t.transaction_date < start
                            and t.odometer_status in TRUSTED_ODOMETER_STATUSES]
            baseline = prior_trusted[-1].odometer_used if prior_trusted else None

            litres = sum((Decimal(str(t.litres or 0)) for t in month_txns),
                        Decimal("0"))
            cost = sum((Decimal(str(t.total_amount or 0)) for t in month_txns),
                      Decimal("0"))

            trusted_this_month = [
                t for t in month_txns
                if t.odometer_status in TRUSTED_ODOMETER_STATUSES]

            end_odometer = None
            km_driven = None
            reason = None

            if not month_txns:
                reason = "NO_ACTIVITY"
            elif not trusted_this_month:
                reason = "UNTRUSTED_ODOMETER"
            else:
                end_odometer = trusted_this_month[-1].odometer_used
                if baseline is None:
                    reason = "NO_BASELINE"
                elif end_odometer < baseline:
                    # Should not happen -- OdometerValidationService
                    # already vets each reading against the vehicle's
                    # own chronological history before it is trusted at
                    # all -- but a report must never SHOW a negative
                    # distance even if some future change upstream
                    # let one through, which is the exact defect this
                    # replaces (a blank month computing as negative).
                    reason = "INVALID_SEQUENCE"
                else:
                    km_driven = end_odometer - baseline

            km_per_litre = None
            cost_per_km = None
            if km_driven is not None and km_driven > 0:
                if litres > 0:
                    km_per_litre = round(Decimal(km_driven) / litres, 2)
                cost_per_km = round(cost / Decimal(km_driven), 2)

            months.append({
                "month": month, "label": MONTH_LABELS[month - 1],
                # The baseline this month measured FROM -- shown
                # alongside End Odometer so a reviewer can see where a
                # month started, not just where it ended. It's the
                # same chained value used for km_driven above, so it
                # stays populated even in a NO_ACTIVITY/UNTRUSTED month
                # (exactly when someone most wants to see what the
                # vehicle's last known reading was); only a vehicle
                # with no trustworthy reading at all, ever, shows None.
                "begin_odometer": baseline,
                "end_odometer": end_odometer, "km_driven": km_driven,
                "litres": litres, "total_cost": cost,
                "km_per_litre": km_per_litre, "cost_per_km": cost_per_km,
                "reason": reason,
            })

        return months

    def monthly_rows(self, year=None, *, branch_id=None, department_id=None,
                     vehicle_type_id=None, fuel_type=None, station=None,
                     plate=None, user=None):
        """One row per vehicle, each with a 12-month series.

        A vehicle is included if it matches the fleet filters, even
        with zero fuel history for the year -- an empty row (every
        month "NO_ACTIVITY") is itself the correct, honest answer for
        a vehicle nobody fuelled, not something to hide.
        """
        year = year or datetime.now().year

        vehicles = self._filtered_vehicle_query(
            branch_id=branch_id, department_id=department_id,
            vehicle_type_id=vehicle_type_id, plate=plate, user=user).all()

        rows = []
        for v in vehicles:
            months = self._vehicle_series(
                v.id, year, fuel_type=fuel_type, station=station)
            rows.append({
                "vehicle_id": v.id,
                "plate_number": v.plate_number or v.conduction_number,
                "vehicle_label": f"{v.brand} {v.model}".strip(),
                "branch_id": v.branch_id,
                "branch_name": v.branch.name if v.branch else None,
                "department_id": v.department_id,
                "department_name": (v.department.name
                                    if v.department else None),
                "months": months,
            })
        return rows

    def fleet_summary(self, year=None, **filters):
        """Fleet-wide totals: litres, cost, and a WEIGHTED Php/km
        (sum of cost over sum of km) -- never an average of each
        vehicle's or month's own ratio, which is exactly the shape of
        mistake the source workbook made at its own summary rows."""
        rows = self.monthly_rows(year=year, **filters)
        total_litres = Decimal("0")
        total_cost = Decimal("0")
        total_km = 0
        for row in rows:
            for m in row["months"]:
                total_litres += m["litres"]
                total_cost += m["total_cost"]
                if m["km_driven"]:
                    total_km += m["km_driven"]

        return {
            "year": year or datetime.now().year,
            "vehicles": len(rows),
            "total_litres": total_litres,
            "total_cost": total_cost,
            "total_km": total_km,
            "km_per_litre": (round(Decimal(total_km) / total_litres, 2)
                            if total_litres > 0 and total_km > 0 else None),
            "cost_per_km": (round(total_cost / Decimal(total_km), 2)
                           if total_km > 0 else None),
        }


REASON_LABELS = {
    "NO_ACTIVITY": "No fuel activity this month",
    "UNTRUSTED_ODOMETER": "No trustworthy odometer reading this month",
    "NO_BASELINE": "No earlier trustworthy reading to measure from",
    "INVALID_SEQUENCE": "Readings out of sequence -- flagged for review",
}

MISSING = "—"   # "-" -- never 0, and never a bare blank cell.

_EXPORT_HEADER = ["Branch", "Department", "Plate / Conduction No.",
                  "Vehicle"]
# Begin Odometer leads the block -- the starting point of the month,
# read before End Odometer or anything derived from it.
_MONTH_BLOCK = ["Begin Odometer", "End Odometer", "KM Driven",
                "Total Fuel (L)", "KM/L", "Cost/KM", "Total Cost"]


def _num_or_missing(value):
    return float(value) if value is not None else MISSING


def export_monthly_analysis_xlsx(rows, year=None, fleet_summary=None) -> bytes:
    """Export the existing server-calculated monthly fuel figures with
    the client's month-by-month color coding. Calculations and exported
    values remain sourced exclusively from monthly_rows()/fleet_summary().
    """
    from openpyxl.utils import get_column_letter

    year = year or datetime.now().year
    wb = Workbook()
    ws = wb.active
    ws.title = f"{year} Fuel Cost & Consumption"

    # Palette follows the supplied monthly workbook: blue vehicle fields,
    # green month bands, and alternating yellow/light-green month blocks.
    title_font = Font(bold=True, size=14, color="17324D")
    white_bold = Font(bold=True, color="FFFFFF")
    dark_font = Font(color="203040")
    month_header_fill = PatternFill("solid", fgColor="00B050")
    identity_header_fill = PatternFill("solid", fgColor="BDD6EE")
    identity_body_fill = PatternFill("solid", fgColor="EAF2F8")
    month_fills = [
        PatternFill("solid", fgColor="FFE598"),  # Jan, Mar, ...
        PatternFill("solid", fgColor="C5E0B3"),  # Feb, Apr, ...
    ]
    border_color = "D5DEE7"
    from openpyxl.styles import Border, Side
    thin_border = Border(
        left=Side(style="thin", color=border_color),
        right=Side(style="thin", color=border_color),
        top=Side(style="thin", color=border_color),
        bottom=Side(style="thin", color=border_color),
    )

    first_month_col = len(_EXPORT_HEADER) + 1
    last_col = len(_EXPORT_HEADER) + 12 * len(_MONTH_BLOCK)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    title_cell = ws.cell(row=1, column=1, value=f"{year} FUEL COST & CONSUMPTION")
    title_cell.font = title_font
    title_cell.alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[1].height = 28

    # Vehicle identification headers span both header rows.
    for col, label in enumerate(_EXPORT_HEADER, start=1):
        ws.merge_cells(start_row=2, start_column=col, end_row=3, end_column=col)
        cell = ws.cell(row=2, column=col, value=label)
        cell.fill = identity_header_fill
        cell.font = Font(bold=True, color="17324D")
        cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        cell.border = thin_border

    # One merged month header per seven-column block, with metric labels below.
    for month_index, label in enumerate(MONTH_LABELS):
        start_col = first_month_col + month_index * len(_MONTH_BLOCK)
        end_col = start_col + len(_MONTH_BLOCK) - 1
        ws.merge_cells(start_row=2, start_column=start_col, end_row=2, end_column=end_col)
        month_cell = ws.cell(row=2, column=start_col, value=label.upper())
        month_cell.fill = month_header_fill
        month_cell.font = white_bold
        month_cell.alignment = Alignment(horizontal="center", vertical="center")
        month_cell.border = thin_border
        for offset, metric_label in enumerate(_MONTH_BLOCK):
            col = start_col + offset
            cell = ws.cell(row=3, column=col, value=metric_label)
            cell.fill = month_fills[month_index % 2]
            cell.font = Font(bold=True, color="203040")
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
            cell.border = thin_border

    ws.row_dimensions[2].height = 24
    ws.row_dimensions[3].height = 38

    # Sensible widths for fixed fields and monthly measurements.
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 21
    ws.column_dimensions["D"].width = 24
    for col in range(first_month_col, last_col + 1):
        offset = (col - first_month_col) % len(_MONTH_BLOCK)
        ws.column_dimensions[get_column_letter(col)].width = 15 if offset in (0, 1, 2) else 14

    # Data values are unchanged; only formatting is applied to the emitted cells.
    for row in rows:
        out = [row.get("branch_name") or MISSING,
               row.get("department_name") or MISSING,
               row.get("plate_number") or MISSING,
               row.get("vehicle_label") or MISSING]
        for m in row["months"]:
            out.extend([
                m["begin_odometer"] if m["begin_odometer"] is not None else MISSING,
                m["end_odometer"] if m["end_odometer"] is not None else MISSING,
                m["km_driven"] if m["km_driven"] is not None else MISSING,
                _num_or_missing(m["litres"]),
                _num_or_missing(m["km_per_litre"]),
                _num_or_missing(m["cost_per_km"]),
                _num_or_missing(m["total_cost"]),
            ])
        ws.append(out)
        row_num = ws.max_row
        ws.row_dimensions[row_num].height = 21
        for col in range(1, last_col + 1):
            cell = ws.cell(row=row_num, column=col)
            cell.font = dark_font
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center", horizontal="left" if col <= 4 else "right")
            if col <= 4:
                cell.fill = identity_body_fill
            else:
                month_index = (col - first_month_col) // len(_MONTH_BLOCK)
                cell.fill = month_fills[month_index % 2]
                metric_offset = (col - first_month_col) % len(_MONTH_BLOCK)
                if isinstance(cell.value, (int, float)):
                    if metric_offset in (0, 1, 2):
                        cell.number_format = '#,##0'
                    elif metric_offset == 3:
                        cell.number_format = '#,##0.0'
                    elif metric_offset == 4:
                        cell.number_format = '#,##0.00'
                    else:
                        cell.number_format = '₱#,##0.00'

    data_last_row = ws.max_row
    if data_last_row >= 4:
        ws.auto_filter.ref = f"A3:{get_column_letter(last_col)}{data_last_row}"
    ws.freeze_panes = "E4"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.orientation = "landscape"
    ws.print_title_rows = "1:3"

    if fleet_summary:
        ws.append([])
        total_row = ws.max_row + 1
        ws.append(["FLEET TOTAL", None, None, None,
                   f"Litres: {fleet_summary.get('total_litres')}",
                   None, None, None,
                   f"Cost: {fleet_summary.get('total_cost')}", None, None, None,
                   f"Php/KM: {_num_or_missing(fleet_summary.get('cost_per_km'))}"])
        for cell in ws[total_row]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F3B4D")
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center")
        ws.row_dimensions[total_row].height = 23

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
