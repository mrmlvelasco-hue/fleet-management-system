"""progress_callback on the three bulk importers.

Only the async background-job path (commit=1, driven through the
POST .../import endpoints -- see test_import_jobs_api.py) actually
passes one; these tests go straight at the service functions to pin
down the exact contract a caller can rely on: called once per counted
data row, in order, with the running total so far (never a percentage
-- the caller already knows total_rows from the job it created).
"""
from io import BytesIO

from openpyxl import Workbook

from app.modules.master_data.driver.import_service import (
    import_drivers, TEMPLATE_COLUMNS as DRIVER_COLUMNS)
from app.modules.master_data.vehicle.import_service import (
    import_vehicles, TEMPLATE_COLUMNS as VEHICLE_COLUMNS)
from app.modules.master_data.vendor.import_service import (
    import_vendors, TEMPLATE_COLUMNS as VENDOR_COLUMNS)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.system_admin.models import SystemParameter


def _sheet(columns, rows, title):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    headers = [c for c, _req, _note in columns]
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _param(db, code, value, group):
    db.session.add(SystemParameter(
        code=code, value=value, data_type="BOOLEAN", group_name=group,
        description="test", is_active=True))
    db.session.commit()


def test_driver_import_calls_progress_callback_once_per_row(db):
    _param(db, "REQUIRE_ASSIGNEE_PHOTO", "false", "DRIVER")
    _param(db, "REQUIRE_DRIVER_LICENSE", "false", "DRIVER")
    BranchService().create(code="BR-CB", name="Callback Branch")
    buf = _sheet(DRIVER_COLUMNS, [
        {"employee_number": "EMP-CB1", "first_name": "A", "last_name": "One",
         "branch_code": "BR-CB"},
        {"employee_number": "EMP-CB2", "first_name": "B", "last_name": "Two",
         "branch_code": "BR-CB"},
    ], "Drivers")

    seen = []
    import_drivers(buf, dry_run=False, progress_callback=seen.append)

    assert seen == [1, 2]


def test_vehicle_import_calls_progress_callback_once_per_row(db):
    BranchService().create(code="BR-CBV", name="Callback Branch")
    VehicleTypeService().create(code="LV-CB", name="Light", category="LIGHT")
    buf = _sheet(VEHICLE_COLUMNS, [
        {"conduction_number": "CB-001", "vehicle_type_code": "LV-CB",
         "branch_code": "BR-CBV", "brand": "Toyota", "model": "Vios",
         "year": 2022},
        {"conduction_number": "CB-002", "vehicle_type_code": "LV-CB",
         "branch_code": "BR-CBV", "brand": "Toyota", "model": "Vios",
         "year": 2023},
    ], "Vehicles")

    seen = []
    import_vehicles(buf, dry_run=False, progress_callback=seen.append)

    assert seen == [1, 2]


def test_vendor_import_calls_progress_callback_once_per_row(db):
    buf = _sheet(VENDOR_COLUMNS, [
        {"code": "VEN-CB1", "name": "Callback Vendor One"},
        {"code": "VEN-CB2", "name": "Callback Vendor Two"},
    ], "Vendors")

    seen = []
    import_vendors(buf, dry_run=False, progress_callback=seen.append)

    assert seen == [1, 2]


def test_progress_callback_is_optional_and_changes_nothing_when_omitted(db):
    buf = _sheet(VENDOR_COLUMNS, [
        {"code": "VEN-NOCB", "name": "No Callback Vendor"},
    ], "Vendors")

    stats = import_vendors(buf, dry_run=False)

    assert stats["created"] == 1
