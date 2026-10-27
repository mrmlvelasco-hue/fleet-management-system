"""Fuel import: the Petron fuel-card statement format.

The client's real sample statement (`Sample_Petron_Fuel_report.xls`)
exposed concrete gaps, each confirmed against the actual importer
before being written up (see the Stage 1 impact analysis):

  * The file is NOT a binary workbook despite its `.xls` extension --
    it is an HTML `<table>` that a provider portal exported with the
    wrong extension. `openpyxl.load_workbook` rejects it outright.
  * Petron's own headers (`TxnDate`, `EmbName`, `BillingTxnAmt`,
    `Trans Slip No`, `Descp`) don't match any existing alias.
  * Petron's date is `M/D/YYYY h:mm:ss AM/PM` -- not one of the
    formats this importer already understood.
  * Petron's `EmbName` has no separator ("AHA9195") while FMS stores
    the same plate with one ("AHA-9195") -- an exact-normalised
    comparison still fails to match them.

And one deliberately-preserved behaviour:
  * The sample has 10 pairs of near-identical rows (same vehicle,
    qty, amount, odometer) a few seconds apart with CONSECUTIVE slip
    numbers. These are genuine separate fills -- the existing
    duplicate key (vehicle, date/time, reference) already keeps them
    apart, since each pair has two different timestamps and two
    different slip numbers.
"""
from io import BytesIO

import pytest

from app.modules.transactions.fuel.import_export import (
    map_headers, _norm, _norm_plate, _to_datetime, import_fuel)


PETRON_HEADER = [
    "AcctNo", "CardNo", "Charging Department", "EmbName", "VehRegsNo",
    "VehRegsNo1", "TxnDate", "Station Name", "Station Address",
    "Trans Slip No", "DriverCd", "Descp", "Qty", "BillingTxnAmt",
    "Odometer",
]


def _petron_html_bytes(rows):
    """Build the same HTML-table-as-.xls shape Petron's own export
    uses: a bare <table>, tab/newline-formatted, no <html> wrapper."""
    def tr(cells):
        return "<tr>\n\t\t" + "".join(f"<td>{c}</td>" for c in cells) + "\n\t</tr>"
    body = "\n".join(tr(r) for r in rows)
    html = (
        '<table cellspacing="0" rules="all" border="1" '
        'style="border-collapse:collapse;">\n\t'
        + tr(PETRON_HEADER) + "\n" + body + "\n</table>"
    )
    return html.encode("utf-8")


def _petron_row(**over):
    base = {
        "AcctNo": "4177", "CardNo": "'738766030234112114",
        "Charging Department": "Main", "EmbName": "AHA9195",
        "VehRegsNo": "", "VehRegsNo1": "",
        "TxnDate": "9/30/2026 1:46:08 PM", "Station Name": "Petron EDSA",
        "Station Address": "", "Trans Slip No": "414282",
        "DriverCd": "2114", "Descp": "Diesel Max", "Qty": "4.4000",
        "BillingTxnAmt": "1000.0000", "Odometer": "415593",
    }
    base.update(over)
    return [base[h] for h in PETRON_HEADER]


# ---------------------------------------------------------------------
# Pure-function checks: header mapping, dates, plate normalisation.
# ---------------------------------------------------------------------

def test_petron_headers_all_map_to_existing_fms_fields():
    mapping = map_headers(PETRON_HEADER)
    for field in ("transaction_date", "plate_number", "card_number",
                 "station", "fuel_type", "litres", "total_amount",
                 "reference_number", "odometer_reported"):
        assert field in mapping, f"{field} did not map from Petron headers"
    # DriverCd is not a driver's name and has no verified lookup -- it
    # must stay unmapped rather than being guessed into driver_name.
    assert "driver_name" not in mapping


def test_petron_datetime_format_is_recognised():
    assert _to_datetime("9/30/2026 1:46:08 PM").isoformat() == \
        "2026-09-30T13:46:08"
    assert _to_datetime("9/7/2026 8:58:34 AM").isoformat() == \
        "2026-09-07T08:58:34"


@pytest.mark.parametrize("a,b", [
    ("AHA-9195", "AHA9195"),
    ("AHA 9195", "AHA9195"),
    ("aha9195", "AHA-9195"),
])
def test_plate_matching_ignores_dashes_and_spaces(a, b):
    assert _norm_plate(a) == _norm_plate(b)


def test_plate_matching_still_distinguishes_different_plates():
    assert _norm_plate("AHA-9195") != _norm_plate("AHA-9196")


# ---------------------------------------------------------------------
# End-to-end import against the real file shape.
# ---------------------------------------------------------------------

@pytest.fixture()
def petron_vehicle(db):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-PET", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-PET", name="Branch Petron")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Mitsubishi", model="Strada",
        year=2022, branch_id=branch.id, conduction_number="PET-1",
        # Stored WITH a dash, the way FMS plates normally look --
        # Petron's own export has none.
        plate_number="AHA-9195")
    db.session.commit()
    return vehicle


def test_an_html_table_saved_as_xls_is_read_despite_the_extension(
        db, petron_vehicle):
    """The exact defect in the sample file: openpyxl rejects it as a
    workbook, so the importer must detect the real content and parse
    it as an HTML table instead of failing the whole upload."""
    raw = _petron_html_bytes([_petron_row()])
    stats = import_fuel(BytesIO(raw), dry_run=True, filename="petron.xls")
    assert stats["errors"] == []
    assert stats["created"] == 1


def test_petron_row_maps_onto_the_matching_vehicle_by_dashless_plate(
        db, petron_vehicle):
    from app.modules.transactions.fuel.models import FuelTransaction
    raw = _petron_html_bytes([_petron_row()])
    import_fuel(BytesIO(raw), dry_run=False, filename="petron.xls")
    txn = FuelTransaction.query.filter_by(vehicle_id=petron_vehicle.id).first()
    assert txn is not None
    assert float(txn.litres) == 4.4
    assert float(txn.total_amount) == 1000.0
    assert txn.odometer_reported == 415593
    assert txn.reference_number == "414282"
    assert txn.station == "Petron EDSA"
    assert txn.transaction_date.isoformat() == "2026-09-30T13:46:08"
    # BillingTxnAmt is preserved as the original total, not recomputed.
    assert float(txn.price_per_litre) == round(1000 / 4.4, 4)


def test_near_identical_rows_seconds_apart_are_both_kept(
        db, petron_vehicle):
    """The ten genuine pairs in the real sample: same vehicle, qty,
    amount and odometer, a few seconds apart, with consecutive slip
    numbers. Both must import -- the existing duplicate key already
    distinguishes them by timestamp and reference number."""
    from app.modules.transactions.fuel.models import FuelTransaction
    raw = _petron_html_bytes([
        _petron_row(**{"Trans Slip No": "414282",
                      "TxnDate": "9/30/2026 1:46:08 PM"}),
        _petron_row(**{"Trans Slip No": "414283",
                      "TxnDate": "9/30/2026 1:46:42 PM"}),
    ])
    stats = import_fuel(BytesIO(raw), dry_run=False, filename="petron.xls")
    assert stats["created"] == 2
    assert stats["skipped"] == 0
    rows = FuelTransaction.query.filter_by(
        vehicle_id=petron_vehicle.id).all()
    assert len(rows) == 2


def test_card_number_apostrophe_prefix_is_stripped_for_matching(db):
    """Petron's CardNo arrives as a text-forced 18-digit string with a
    leading apostrophe. A FuelCard registered with the plain digits
    must still match it."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.fuel.models import FuelCard, FuelTransaction
    from app.extensions import db as _db

    vt = VehicleTypeService().create(code="LV-CARD", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-CARD", name="Branch Card")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="CARD-1")
    card = FuelCard(card_number="738766030234112114", provider="PETRON",
                    vehicle_id=vehicle.id, status="ACTIVE")
    _db.session.add(card)
    _db.session.commit()

    raw = _petron_html_bytes([_petron_row(EmbName="")])
    import_fuel(BytesIO(raw), dry_run=False, filename="petron.xls")
    txn = FuelTransaction.query.filter_by(vehicle_id=vehicle.id).first()
    assert txn is not None
    assert txn.fuel_card_id == card.id


def test_unmatched_vehicle_is_reported_not_auto_created(db):
    """§5 of the master prompt: never create a vehicle just because a
    plate didn't match."""
    from app.modules.master_data.vehicle.models import Vehicle
    before = Vehicle.query.count()
    raw = _petron_html_bytes([_petron_row(EmbName="ZZZ9999")])
    stats = import_fuel(BytesIO(raw), dry_run=True, filename="petron.xls")
    assert stats["skipped"] == 1
    assert "no vehicle matches" in stats["errors"][0]["problems"][0]
    assert Vehicle.query.count() == before


def test_blank_and_zero_quantity_rows_are_rejected_safely(db, petron_vehicle):
    raw = _petron_html_bytes([
        _petron_row(Qty="0"), _petron_row(Qty=""),
    ])
    stats = import_fuel(BytesIO(raw), dry_run=True, filename="petron.xls")
    assert stats["created"] == 0
    assert stats["skipped"] == 2
    for e in stats["errors"]:
        assert "litres" in e["problems"][0]


def test_html_file_with_no_table_fails_clearly_not_as_a_zip_error(db):
    raw = b"<html><body><p>no table here</p></body></html>"
    with pytest.raises(ValueError, match="(?i)table"):
        import_fuel(BytesIO(raw), dry_run=True, filename="petron.xls")
