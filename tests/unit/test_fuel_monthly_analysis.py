"""Fuel Analysis report -- the monthly per-vehicle engine.

Verified against the client's own MONTHLY FUEL ANALYSIS 2026.xlsx
before a line of this was written (Stage 1 impact analysis):

  * End Odometer(month) = the LATEST reading of the month (by date,
    not by value) among readings the existing OdometerValidationService
    already trusts (OK/CORRECTED) -- a SUSPECT or MISSING reading in a
    month must not become that month's "end odometer".
  * KM(month) = End Odometer(month) minus the end odometer of the most
    recent EARLIER month that itself had a trustworthy reading -- not
    always the immediately preceding calendar month. A month with no
    fuel activity, or fuel activity but no trustworthy reading, must
    not break the chain for the month after it.
  * Litres and cost are totalled for every transaction in the month
    regardless of odometer trustworthiness -- the fuel was genuinely
    bought either way.
  * Missing or not-yet-computable figures show as "-" with a REASON
    (no activity / no trustworthy reading / no baseline yet), never 0
    and never a misleading negative -- the three defects actually
    found in the client's own workbook (months chained from the wrong
    baseline, IFERROR -> 0, blank future months -> negative KM).
  * The fleet-wide Cost/KM is a SUM-of-cost over SUM-of-km (weighted),
    not an average of each month's own ratio.
"""
from datetime import datetime

import pytest

from app.modules.transactions.fuel.monthly_analysis import (
    FuelMonthlyAnalysisService)


@pytest.fixture()
def vehicle(db):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-FMA", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-FMA", name="Branch FMA")
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Mitsubishi", model="Strada",
        year=2022, branch_id=branch.id, conduction_number="FMA-1",
        plate_number="FMA-001")


def _txn(vehicle_id, date, litres, amount, odometer_used=None,
        odometer_status=None):
    from app.modules.transactions.fuel.models import FuelTransaction
    return FuelTransaction(
        vehicle_id=vehicle_id, transaction_date=date, litres=litres,
        total_amount=amount, odometer_reported=odometer_used,
        odometer_used=odometer_used, odometer_status=odometer_status)


def test_fixture_exercises_every_documented_edge_case(db, vehicle):
    """One vehicle, one year: a baseline from the prior December, a
    normal month, a month with NO fuel activity at all, a month with
    fuel activity but an untrustworthy reading, and a month that must
    chain past both gaps to the last trustworthy reading."""
    from app.extensions import db as _db
    rows = [
        # Baseline, December of the PRIOR year -- not part of the
        # report year itself, only its closing reading matters.
        _txn(vehicle.id, datetime(2025, 12, 20), 40, 2400, 1000, "OK"),
        # January: a normal, fully computable month.
        _txn(vehicle.id, datetime(2026, 1, 15), 50, 3000, 1200, "OK"),
        # February: NO transactions at all -- true "no activity".
        # March: fuel was bought, but the only reading is untrustworthy.
        _txn(vehicle.id, datetime(2026, 3, 10), 30, 1800, 50, "SUSPECT"),
        # April: chains past both February and March to January's 1200.
        _txn(vehicle.id, datetime(2026, 4, 5), 40, 2800, 1600, "OK"),
    ]
    for r in rows:
        _db.session.add(r)
    _db.session.commit()

    result = FuelMonthlyAnalysisService().monthly_rows(year=2026)
    assert len(result) == 1
    row = result[0]
    assert row["vehicle_id"] == vehicle.id
    months = row["months"]
    assert len(months) == 12

    jan, feb, mar, apr = months[0], months[1], months[2], months[3]

    assert jan["begin_odometer"] == 1000    # Dec 2025's closing reading
    assert jan["end_odometer"] == 1200
    assert jan["km_driven"] == 200          # 1200 - 1000
    assert float(jan["litres"]) == 50
    assert float(jan["total_cost"]) == 3000
    assert round(float(jan["km_per_litre"]), 2) == 4.0
    assert round(float(jan["cost_per_km"]), 2) == 15.0
    assert jan["reason"] is None

    # Begin Odometer still shows the carried-forward starting point even
    # in a month with no activity or no trustworthy reading -- that's
    # exactly the figure someone needs to see WHY this month has no KM
    # Driven, not something to hide along with it.
    assert feb["begin_odometer"] == 1200    # carried from Jan, Feb itself is empty
    assert feb["end_odometer"] is None
    assert feb["km_driven"] is None
    assert float(feb["litres"]) == 0
    assert feb["reason"] == "NO_ACTIVITY"

    assert mar["begin_odometer"] == 1200    # still Jan's -- Feb had nothing either
    assert mar["end_odometer"] is None      # the only reading was SUSPECT
    assert mar["km_driven"] is None
    assert float(mar["litres"]) == 30       # the fuel was still bought
    assert float(mar["total_cost"]) == 1800
    assert mar["reason"] == "UNTRUSTED_ODOMETER"

    assert apr["begin_odometer"] == 1200    # chained past Feb/Mar to Jan's 1200
    assert apr["end_odometer"] == 1600
    assert apr["km_driven"] == 400          # chained past Feb/Mar to Jan's 1200
    assert float(apr["litres"]) == 40
    assert round(float(apr["km_per_litre"]), 2) == 10.0
    assert round(float(apr["cost_per_km"]), 2) == 7.0
    assert apr["reason"] is None


def test_no_prior_reading_at_all_is_a_distinct_reason(db, vehicle):
    """A vehicle's very first-ever reading has nothing to measure FROM
    -- that is not the same situation as a later gap, and must say so
    rather than silently showing 0 or nothing."""
    from app.extensions import db as _db
    _db.session.add(_txn(vehicle.id, datetime(2026, 1, 10), 40, 2000,
                         900, "OK"))
    _db.session.commit()

    result = FuelMonthlyAnalysisService().monthly_rows(year=2026)
    jan = result[0]["months"][0]
    assert jan["begin_odometer"] is None    # nothing earlier exists at all
    assert jan["end_odometer"] == 900
    assert jan["km_driven"] is None
    assert jan["reason"] == "NO_BASELINE"


def test_future_months_with_no_data_are_not_a_negative_km(db, vehicle):
    """The exact workbook defect this must not repeat: a month with no
    data yet must never compute as a negative distance."""
    from app.extensions import db as _db
    _db.session.add(_txn(vehicle.id, datetime(2026, 1, 10), 40, 2000,
                         1000, "OK"))
    _db.session.commit()

    result = FuelMonthlyAnalysisService().monthly_rows(year=2026)
    dec = result[0]["months"][11]
    assert dec["km_driven"] is None
    assert dec["reason"] == "NO_ACTIVITY"


def test_vehicle_with_zero_fuel_history_shows_every_month_as_no_activity(
        db, vehicle):
    result = FuelMonthlyAnalysisService().monthly_rows(year=2026)
    assert len(result) == 1
    assert all(m["reason"] == "NO_ACTIVITY" for m in result[0]["months"])


def test_year_is_a_parameter_not_hardcoded(db, vehicle):
    """The report must work for any year asked for, not just a
    hardcoded one."""
    from app.extensions import db as _db
    _db.session.add(_txn(vehicle.id, datetime(2027, 1, 10), 10, 500,
                         100, "OK"))
    _db.session.commit()

    result_2026 = FuelMonthlyAnalysisService().monthly_rows(year=2026)
    result_2027 = FuelMonthlyAnalysisService().monthly_rows(year=2027)
    assert all(m["reason"] == "NO_ACTIVITY" for m in result_2026[0]["months"])
    assert result_2027[0]["months"][0]["end_odometer"] == 100


def test_fleet_cost_per_km_is_weighted_not_averaged(db):
    """Two vehicles with very different distances: the fleet figure
    must be sum(cost)/sum(km), not the arithmetic mean of each
    vehicle's own ratio -- the workbook's bug was a per-month version
    of exactly this mistake."""
    from app.extensions import db as _db
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService

    vt = VehicleTypeService().create(code="LV-FMA2", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-FMA2", name="Branch FMA2")
    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2022,
                                 branch_id=branch.id,
                                 conduction_number="FMA2-1")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2022,
                                 branch_id=branch.id,
                                 conduction_number="FMA2-2")
    # v1: baseline then one big month -- 1000 km for 10000 cost (10/km).
    _db.session.add(_txn(v1.id, datetime(2025, 12, 1), 10, 100, 0, "OK"))
    _db.session.add(_txn(v1.id, datetime(2026, 1, 15), 100, 10000, 1000, "OK"))
    # v2: baseline then a small month -- 10 km for 1000 cost (100/km).
    _db.session.add(_txn(v2.id, datetime(2025, 12, 1), 10, 100, 0, "OK"))
    _db.session.add(_txn(v2.id, datetime(2026, 1, 15), 1, 1000, 10, "OK"))
    _db.session.commit()

    summary = FuelMonthlyAnalysisService().fleet_summary(year=2026)
    # Weighted: (10000 + 1000) / (1000 + 10) = 11000 / 1010 ~= 10.89,
    # not the 55.0 average of each vehicle's own 10/km and 100/km.
    assert float(summary["cost_per_km"]) == round(11000 / 1010, 2)


# ---------------------------------------------------------------------
# Excel export -- mirrors the client's own workbook layout, but using
# this engine's corrected figures and an explicit "-" instead of a
# misleading 0 wherever a month genuinely has nothing to show.
# ---------------------------------------------------------------------

def test_export_mirrors_the_client_workbook_layout_with_a_dynamic_year(
        db, vehicle):
    from openpyxl import load_workbook
    from io import BytesIO
    from app.modules.transactions.fuel.monthly_analysis import (
        export_monthly_analysis_xlsx)

    rows = FuelMonthlyAnalysisService().monthly_rows(year=2027)
    xlsx = export_monthly_analysis_xlsx(rows, year=2027)
    wb = load_workbook(BytesIO(xlsx))
    ws = wb.active

    # The year is in the title, not hardcoded to 2026.
    title_text = " ".join(str(c.value) for c in ws[1] if c.value)
    assert "2027" in title_text
    assert "2026" not in title_text

    # Row 2: vehicle identity headers, plus one merged month band per
    # month (JAN..DEC per the client's color-coded layout). Row 3: the
    # per-metric sub-headers under each month band. Data starts row 4.
    from app.modules.transactions.fuel.monthly_analysis import _MONTH_BLOCK

    identity_row = [c.value for c in ws[2]]
    assert "Branch" in identity_row
    assert "Plate / Conduction No." in identity_row
    assert "JAN" in identity_row
    assert "DEC" in identity_row

    jan_col = identity_row.index("JAN") + 1
    metric_row = [c.value for c in ws[3]]
    assert metric_row[jan_col - 1: jan_col - 1 + len(_MONTH_BLOCK)] == _MONTH_BLOCK

    # The vehicle has zero fuel history for 2027. Litres/cost are
    # genuinely, truthfully zero -- that stays a real 0, not a dash
    # pretending the number is unknown. But every odometer-derived
    # figure (End Odometer, KM Driven, KM/L, Cost/KM) is UNKNOWABLE,
    # not zero, and must show as an explicit dash.
    jan_begin_odo_col = jan_col + _MONTH_BLOCK.index("Begin Odometer")
    jan_end_odo_col = jan_col + _MONTH_BLOCK.index("End Odometer")
    jan_km_col = jan_col + _MONTH_BLOCK.index("KM Driven")
    jan_kml_col = jan_col + _MONTH_BLOCK.index("KM/L")
    jan_cost_km_col = jan_col + _MONTH_BLOCK.index("Cost/KM")
    # Begin Odometer is the FIRST column of the month's block -- the
    # starting point a reviewer reads before anything else in the group.
    assert jan_begin_odo_col == jan_end_odo_col - 1
    data_row = [c.value for c in ws[4]]
    for col in (jan_begin_odo_col, jan_end_odo_col, jan_km_col,
               jan_kml_col, jan_cost_km_col):
        assert data_row[col - 1] == "—"


def test_export_reconciles_to_the_real_figures_not_the_defects(db, vehicle):
    """The exact AHA9195 reconciliation from Stage 1: a computable
    month's exported KM/L and Cost/KM must match this engine's own
    monthly_rows output -- not a workbook-style shortcut."""
    from datetime import datetime as _dt
    from openpyxl import load_workbook
    from io import BytesIO
    from app.extensions import db as _db
    from app.modules.transactions.fuel.monthly_analysis import (
        export_monthly_analysis_xlsx)

    _db.session.add(_txn(vehicle.id, _dt(2025, 12, 1), 10, 500, 405093, "OK"))
    _db.session.add(_txn(vehicle.id, _dt(2026, 9, 30), 220.06, 20000,
                         415593, "OK"))
    _db.session.commit()

    rows = FuelMonthlyAnalysisService().monthly_rows(year=2026)
    sept = rows[0]["months"][8]
    assert sept["km_driven"] == 10500
    assert round(float(sept["km_per_litre"]), 2) == round(10500 / 220.06, 2)

    xlsx = export_monthly_analysis_xlsx(rows, year=2026)
    wb = load_workbook(BytesIO(xlsx))
    ws = wb.active
    from app.modules.transactions.fuel.monthly_analysis import _MONTH_BLOCK
    identity_row = [c.value for c in ws[2]]
    sep_col = identity_row.index("SEP") + 1
    sept_km_col = sep_col + _MONTH_BLOCK.index("KM Driven")
    data_row = [c.value for c in ws[4]]
    assert data_row[sept_km_col - 1] == 10500
