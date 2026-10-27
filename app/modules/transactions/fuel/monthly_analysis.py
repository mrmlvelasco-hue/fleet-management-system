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
_MONTH_BLOCK = ["End Odometer", "KM Driven", "Total Fuel (L)", "KM/L",
                "Cost/KM", "Total Cost"]


def _num_or_missing(value):
    return float(value) if value is not None else MISSING


def export_monthly_analysis_xlsx(rows, year=None, fleet_summary=None) -> bytes:
    """The web report's own figures, laid out the way the client's
    MONTHLY FUEL ANALYSIS workbook is laid out -- one row per vehicle,
    a 6-column block per month -- but using THIS engine's corrected
    monthly_rows()/fleet_summary() output, not the workbook's own
    formulas. A month with nothing computable writes "-", never a 0
    that would read as zero consumption.
    """
    year = year or datetime.now().year
    wb = Workbook()
    ws = wb.active
    ws.title = f"{year} Fuel Cost & Consumption"

    title_font = Font(bold=True, size=13)
    header_fill = PatternFill("solid", fgColor="1F3B4D")
    header_font = Font(bold=True, color="FFFFFF")

    ws.merge_cells(start_row=1, start_column=1, end_row=1,
                   end_column=len(_EXPORT_HEADER) + 12 * len(_MONTH_BLOCK))
    ws.cell(row=1, column=1,
           value=f"{year} FUEL COST & CONSUMPTION").font = title_font

    header_row = list(_EXPORT_HEADER)
    for label in MONTH_LABELS:
        header_row.extend(f"{label} {col}" for col in _MONTH_BLOCK)
    ws.append([None])     # placeholder so append lands on row 2
    for i, text in enumerate(header_row, start=1):
        cell = ws.cell(row=2, column=i, value=text)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, horizontal="center")
    ws.row_dimensions[2].height = 30
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 22

    for row in rows:
        out = [row.get("branch_name") or MISSING,
              row.get("department_name") or MISSING,
              row.get("plate_number") or MISSING,
              row.get("vehicle_label") or MISSING]
        for m in row["months"]:
            # Litres and cost are real, summed totals for the month --
            # genuinely zero when nothing was bought, which is a true
            # fact, not a gap. End Odometer/KM/KM-L/Cost-KM are each
            # UNKNOWABLE (not zero) whenever the month has no reason
            # to trust a figure at all.
            out.extend([
                m["end_odometer"] if m["end_odometer"] is not None else MISSING,
                m["km_driven"] if m["km_driven"] is not None else MISSING,
                _num_or_missing(m["litres"]),
                _num_or_missing(m["km_per_litre"]),
                _num_or_missing(m["cost_per_km"]),
                _num_or_missing(m["total_cost"]),
            ])
        ws.append(out)

    if fleet_summary:
        ws.append([])
        ws.append(["FLEET TOTAL", None, None, None,
                  f"Litres: {fleet_summary.get('total_litres')}",
                  None, None, None,
                  f"Cost: {fleet_summary.get('total_cost')}", None, None, None,
                  f"Php/KM: {_num_or_missing(fleet_summary.get('cost_per_km'))}"])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
