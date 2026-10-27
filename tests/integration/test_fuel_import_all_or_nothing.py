"""Fuel import commit behaviour: configurable all-or-nothing vs partial.

Michael's decision (2026-10-27): all-or-nothing is the default, but a
failed commit must show a clear, specific summary of what went wrong --
not just "nothing happened". This is a SystemParameter
(`FUEL_IMPORT_MODE`), not a hardcoded rule, per the project's
configurable-over-hardcoded principle: a client can still opt into the
previous partial-import behaviour if that ever becomes the right call
for them.

The preview (dry_run=True) already lists every row's problems before
anything commits -- this file pins the COMMIT path, which previously
saved the good rows regardless of how many others failed.
"""
from io import BytesIO

import openpyxl
import pytest


def _wb_bytes(rows, header=None):
    header = header or ["transaction_date", "plate_number", "litres",
                       "total_amount", "reference_number"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fuel"
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@pytest.fixture()
def two_vehicles(db):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-AON", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-AON", name="Branch AON")
    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Vios", year=2022, branch_id=branch.id,
                                 conduction_number="AON-1",
                                 plate_number="AON-001")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Vios", year=2022, branch_id=branch.id,
                                 conduction_number="AON-2",
                                 plate_number="AON-002")
    db.session.commit()
    return v1, v2


def test_default_mode_is_all_or_nothing(db):
    from app.modules.system_admin.services.system_parameter_service import (
        SystemParameterService)
    assert SystemParameterService().get(
        "FUEL_IMPORT_MODE", "ALL_OR_NOTHING") == "ALL_OR_NOTHING"


def test_seed_all_registers_the_parameter_so_admins_can_edit_it(db):
    """Not just a code-level default -- it must show up in System
    Administration's parameter list, the same way every other
    configurable business rule in this app does."""
    from app.cli import _seed_system_parameters
    from app.modules.system_admin.models import SystemParameter
    _seed_system_parameters()
    db.session.commit()
    param = SystemParameter.query.filter_by(code="FUEL_IMPORT_MODE").first()
    assert param is not None
    assert param.value == "ALL_OR_NOTHING"
    assert param.group_name == "FUEL"


def test_a_file_with_one_bad_row_saves_nothing_by_default(db, two_vehicles):
    from app.modules.transactions.fuel.import_export import import_fuel
    from app.modules.transactions.fuel.models import FuelTransaction

    v1, v2 = two_vehicles
    rows = [
        ["2026-09-01 08:00", v1.plate_number, 10, 500, "OK-1"],
        ["2026-09-01 09:00", v2.plate_number, "", 500, "BAD-1"],  # no litres
    ]
    stats = import_fuel(_wb_bytes(rows), dry_run=False,
                        filename="statement.xlsx")

    assert stats["created"] == 0
    assert stats["committed"] is False
    assert stats["import_batch_id"] is None
    assert FuelTransaction.query.count() == 0
    # The clear, specific summary the person asked for.
    assert stats["message"]
    assert "1" in stats["message"]
    assert len(stats["errors"]) == 1
    assert "litres" in stats["errors"][0]["problems"][0]


def test_a_clean_file_commits_normally_in_all_or_nothing_mode(
        db, two_vehicles):
    from app.modules.transactions.fuel.import_export import import_fuel
    from app.modules.transactions.fuel.models import FuelTransaction

    v1, v2 = two_vehicles
    rows = [
        ["2026-09-01 08:00", v1.plate_number, 10, 500, "OK-1"],
        ["2026-09-01 09:00", v2.plate_number, 12, 600, "OK-2"],
    ]
    stats = import_fuel(_wb_bytes(rows), dry_run=False,
                        filename="statement.xlsx")
    assert stats["created"] == 2
    assert stats["committed"] is True
    assert stats["import_batch_id"] is not None
    assert FuelTransaction.query.count() == 2


def test_duplicate_rows_do_not_by_themselves_block_the_rest(
        db, two_vehicles):
    """A duplicate is a defensive, expected skip -- not the kind of
    data error all-or-nothing exists to catch. Re-uploading a
    statement that overlaps a previous one must still save the new
    rows."""
    from app.modules.transactions.fuel.import_export import import_fuel
    from app.modules.transactions.fuel.models import FuelTransaction

    v1, v2 = two_vehicles
    first = [["2026-09-01 08:00", v1.plate_number, 10, 500, "DUP-1"]]
    import_fuel(_wb_bytes(first), dry_run=False, filename="s1.xlsx")
    assert FuelTransaction.query.count() == 1

    rows = [
        ["2026-09-01 08:00", v1.plate_number, 10, 500, "DUP-1"],  # dup
        ["2026-09-01 09:00", v2.plate_number, 12, 600, "NEW-1"],  # new
    ]
    stats = import_fuel(_wb_bytes(rows), dry_run=False,
                        filename="s2.xlsx")
    assert stats["created"] == 1
    assert stats["committed"] is True
    assert FuelTransaction.query.count() == 2


def test_partial_mode_keeps_the_good_rows_when_configured(
        db, two_vehicles):
    from app.modules.system_admin.services.system_parameter_service import (
        SystemParameterService)
    from app.modules.system_admin.models import SystemParameter
    from app.extensions import db as _db
    from app.modules.transactions.fuel.import_export import import_fuel
    from app.modules.transactions.fuel.models import FuelTransaction

    _db.session.add(SystemParameter(
        code="FUEL_IMPORT_MODE", value="PARTIAL", data_type="STRING",
        group_name="FUEL", description="test override"))
    _db.session.commit()
    assert SystemParameterService().get("FUEL_IMPORT_MODE") == "PARTIAL"

    v1, v2 = two_vehicles
    rows = [
        ["2026-09-01 08:00", v1.plate_number, 10, 500, "OK-1"],
        ["2026-09-01 09:00", v2.plate_number, "", 500, "BAD-1"],
    ]
    stats = import_fuel(_wb_bytes(rows), dry_run=False,
                        filename="statement.xlsx")
    assert stats["created"] == 1
    assert stats["committed"] is True
    assert stats["import_batch_id"] is not None
    assert FuelTransaction.query.count() == 1
    assert len(stats["errors"]) == 1


def test_preview_lists_every_problem_before_anything_commits(
        db, two_vehicles):
    """dry_run must already surface the same clear per-row summary, so
    the person can fix the file before import, not just after a
    rejected commit."""
    from app.modules.transactions.fuel.import_export import import_fuel
    from app.modules.transactions.fuel.models import FuelTransaction

    v1, v2 = two_vehicles
    rows = [
        ["2026-09-01 08:00", v1.plate_number, 10, 500, "OK-1"],
        ["2026-09-01 09:00", v2.plate_number, "", 500, "BAD-1"],
    ]
    stats = import_fuel(_wb_bytes(rows), dry_run=True)
    assert FuelTransaction.query.count() == 0
    assert len(stats["errors"]) == 1
    assert stats["message"]
