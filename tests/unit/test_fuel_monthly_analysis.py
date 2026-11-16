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
    assert "Company" in identity_row
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


def test_monthly_rows_resolves_company_through_the_vehicles_branch(db):
    """Michael's UAT report (2026-11-09): the on-screen/export identity
    column showed 'Branch' even though this report's only org filter is
    Company (no Branch filter exists on this screen at all) -- so a
    reader had no way to see which Company a row belonged to, only a
    Branch, which this report doesn't let them filter or search by.

    The vehicle itself has no company_id of its own here -- only its
    Branch is mapped to a Company -- exercising the EFFECTIVE-company
    fallback (app.core.company_resolution), the same rule every other
    Company-aware report/filter in the system already uses."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import (
        BranchService, RegionService, AreaDistrictService,
        OrgCompanyService)
    from app.modules.master_data.vehicle.service import VehicleService

    region = RegionService().create(code="RG-FMA", name="Region FMA")
    area = AreaDistrictService().create(
        code="AR-FMA", name="Area FMA", region_id=region.id)
    company = OrgCompanyService().create(
        short_name="Acme", region_id=region.id, area_district_id=area.id)
    branch = BranchService().create(
        code="BR-FMA3", name="Branch FMA3", company_id=company.id)
    vt = VehicleTypeService().create(code="LV-FMA3", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
        year=2023, branch_id=branch.id, conduction_number="FMA-3",
        plate_number="FMA-003")

    result = FuelMonthlyAnalysisService().monthly_rows(year=2026)
    row = next(r for r in result if r["vehicle_id"] == vehicle.id)

    assert row["company_id"] == company.id
    assert row["company_name"] == "Acme (Region FMA / Area FMA)"


def test_monthly_rows_company_is_none_when_branch_is_unmapped(db, vehicle):
    """A legacy, not-yet-mapped Branch is the normal starting state
    (per the Branch->Company migration docs) -- must render as a clean
    None/'-', never raise or silently default to something else."""
    result = FuelMonthlyAnalysisService().monthly_rows(year=2026)
    row = next(r for r in result if r["vehicle_id"] == vehicle.id)
    assert row["company_id"] is None
    assert row["company_name"] is None


def test_xlsx_export_identity_column_is_company_not_branch(db):
    from io import BytesIO
    from openpyxl import load_workbook
    from app.modules.transactions.fuel.monthly_analysis import (
        export_monthly_analysis_xlsx)

    rows = FuelMonthlyAnalysisService().monthly_rows(year=2027)
    xlsx = export_monthly_analysis_xlsx(rows, year=2027)
    wb = load_workbook(BytesIO(xlsx))
    ws = wb.active

    identity_row = [c.value for c in ws[2]]
    assert "Company" in identity_row
    assert "Branch" not in identity_row


# ---------------------------------------------------------------------
# Opening month (2026-11-16) -- a vehicle's first month of fuel data.
#
# Michael's UAT: DCF8365's whole fuel history is October 2026, so the
# report showed "-" for KM Driven, KM/L and Cost/KM (NO_BASELINE).
# Client decision: measure the first month from its OWN first reading,
# and leave the fill(s) taken at that reading out of the two ratios --
# that fuel was bought before the measured distance was driven. Total
# Fuel and Total Cost still show everything that was bought.
# ---------------------------------------------------------------------

_DCF8365_OCTOBER = [
    # (day, hh, mm, ss, litres, amount, odometer) -- the client's own
    # October card statement for DCF8365, in date order.
    (1, 11, 31, 24, 21.30, 2000.00, 25363),
    (1, 11, 31, 51, 21.29, 1999.41, 25363),
    (3, 11, 19, 32, 20.45, 2000.00, 25932),
    (3, 11, 19, 56, 20.45, 2000.00, 25932),
    (5, 12, 51, 26, 21.30, 2000.00, 26478),
    (5, 12, 53, 51, 21.30, 2000.00, 26478),
    (8, 14, 49, 44, 21.39, 2000.00, 26891),
    (8, 14, 51, 50, 21.39, 2000.00, 26891),
    (10, 14, 35, 44, 21.60, 2000.00, 27121),
    (19, 8, 39, 43, 22.32, 2000.00, 27295),
    (19, 8, 42, 42, 14.58, 1306.19, 27295),
    (23, 8, 52, 41, 11.72, 1080.58, 27867),
    (23, 10, 0, 51, 21.58, 2000.00, 27905),
    (24, 12, 46, 41, 21.53, 2000.00, 28177),
    (24, 12, 47, 2, 21.53, 2000.00, 28177),
    (30, 8, 40, 35, 21.41, 2000.00, 28893),
    (30, 8, 43, 24, 21.41, 2000.00, 28893),
]


def _add_dcf8365_october(vehicle_id):
    from app.extensions import db as _db
    for day, hh, mm, ss, litres, amount, odo in _DCF8365_OCTOBER:
        _db.session.add(_txn(vehicle_id, datetime(2026, 10, day, hh, mm, ss),
                             litres, amount, odo, "OK"))
    _db.session.commit()


def test_opening_month_measures_from_its_own_first_reading(db, vehicle):
    _add_dcf8365_october(vehicle.id)

    octo = FuelMonthlyAnalysisService().monthly_rows(year=2026)[0]["months"][9]

    assert octo["begin_odometer"] == 25363
    assert octo["end_odometer"] == 28893
    assert octo["km_driven"] == 3530
    assert octo["reason"] is None
    assert octo["opening_month"] is True
    # Everything bought in the month is still totalled.
    assert round(float(octo["litres"]), 2) == 346.55
    assert round(float(octo["total_cost"]), 2) == 32386.18
    # The two fills at the opening reading (42.59 L, 3,999.41) are left
    # out of the ratios: 3530 / 303.96 and 28,386.77 / 3530.
    assert round(float(octo["opening_litres"]), 2) == 42.59
    assert round(float(octo["opening_cost"]), 2) == 3999.41
    assert float(octo["km_per_litre"]) == 11.61
    assert float(octo["cost_per_km"]) == 8.04


def test_month_after_the_opening_month_is_calculated_as_before(db, vehicle):
    from app.extensions import db as _db
    _add_dcf8365_october(vehicle.id)
    _db.session.add(_txn(vehicle.id, datetime(2026, 11, 5), 40, 3600,
                         29293, "OK"))
    _db.session.commit()

    nov = FuelMonthlyAnalysisService().monthly_rows(year=2026)[0]["months"][10]

    assert nov["begin_odometer"] == 28893
    assert nov["km_driven"] == 400
    assert nov["opening_month"] is False
    assert float(nov["opening_litres"]) == 0
    assert float(nov["km_per_litre"]) == 10.0
    assert float(nov["cost_per_km"]) == 9.0


def test_a_month_with_an_earlier_baseline_is_never_an_opening_month(
        db, vehicle):
    """The opening rule applies ONLY when no earlier trustworthy reading
    exists. A normal month keeps every litre in its ratios."""
    from app.extensions import db as _db
    _db.session.add(_txn(vehicle.id, datetime(2025, 12, 20), 40, 2400,
                         1000, "OK"))
    _db.session.add(_txn(vehicle.id, datetime(2026, 1, 5), 20, 1200,
                         1100, "OK"))
    _db.session.add(_txn(vehicle.id, datetime(2026, 1, 15), 30, 1800,
                         1200, "OK"))
    _db.session.commit()

    jan = FuelMonthlyAnalysisService().monthly_rows(year=2026)[0]["months"][0]

    assert jan["opening_month"] is False
    assert jan["begin_odometer"] == 1000
    assert jan["km_driven"] == 200
    assert float(jan["km_per_litre"]) == 4.0      # 200 / (20 + 30)


def test_opening_month_ignores_untrusted_readings(db, vehicle):
    """The opening reading is the first TRUSTED one. Fuel bought before
    it (here on an unreadable odometer) is opening fuel too."""
    from app.extensions import db as _db
    _db.session.add(_txn(vehicle.id, datetime(2026, 3, 1), 10, 900,
                         5, "SUSPECT"))
    _db.session.add(_txn(vehicle.id, datetime(2026, 3, 2), 20, 1800,
                         5000, "OK"))
    _db.session.add(_txn(vehicle.id, datetime(2026, 3, 20), 25, 2500,
                         5300, "OK"))
    _db.session.commit()

    mar = FuelMonthlyAnalysisService().monthly_rows(year=2026)[0]["months"][2]

    assert mar["begin_odometer"] == 5000
    assert mar["km_driven"] == 300
    assert float(mar["opening_litres"]) == 30     # 10 + 20
    assert float(mar["km_per_litre"]) == 12.0     # 300 / 25
    assert float(mar["cost_per_km"]) == round(2500 / 300, 2)


def test_opening_month_with_no_distance_yet_stays_not_computable(db, vehicle):
    """Every fill of the first month at the same reading: there is still
    no distance to measure, so it stays a dash with its reason."""
    from app.extensions import db as _db
    _db.session.add(_txn(vehicle.id, datetime(2026, 1, 10, 9, 0), 20, 1000,
                         900, "OK"))
    _db.session.add(_txn(vehicle.id, datetime(2026, 1, 10, 9, 2), 20, 1000,
                         900, "OK"))
    _db.session.commit()

    jan = FuelMonthlyAnalysisService().monthly_rows(year=2026)[0]["months"][0]

    assert jan["km_driven"] is None
    assert jan["km_per_litre"] is None
    assert jan["reason"] == "NO_BASELINE"
    assert jan["opening_month"] is False


def test_fleet_summary_ratios_leave_out_opening_fuel(db, vehicle):
    """The header cards must agree with the row beneath them: for a
    one-vehicle fleet they are the same numbers."""
    _add_dcf8365_october(vehicle.id)

    summary = FuelMonthlyAnalysisService().fleet_summary(year=2026)

    assert summary["total_km"] == 3530
    assert round(float(summary["total_litres"]), 2) == 346.55
    assert round(float(summary["total_cost"]), 2) == 32386.18
    assert float(summary["km_per_litre"]) == 11.61
    assert float(summary["cost_per_km"]) == 8.04


def test_opening_month_with_a_blank_reading_does_not_crash(db, vehicle):
    """A fill marked OK but carrying no odometer value must degrade to
    'not computable', never raise."""
    from app.extensions import db as _db
    _db.session.add(_txn(vehicle.id, datetime(2026, 2, 3), 20, 1000,
                         None, "OK"))
    _db.session.add(_txn(vehicle.id, datetime(2026, 2, 9), 20, 1000,
                         None, "OK"))
    _db.session.commit()

    feb = FuelMonthlyAnalysisService().monthly_rows(year=2026)[0]["months"][1]

    assert feb["km_driven"] is None
    assert feb["reason"] == "NO_BASELINE"
    assert float(feb["litres"]) == 40
