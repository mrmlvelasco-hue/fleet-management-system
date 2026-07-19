"""Excel report generators.

Each function takes an optional `filters` dict and returns
(filename: str, xlsx_bytes: bytes). Kept separate from the Flask routes
so the exact same code path produces the file whether a person clicks
"Export to Excel" in the browser or a ScheduledReport emails it
unattended overnight — the two were previously at risk of drifting apart
since the manual export lived inline in a route.

Supported filter keys (all optional; a report ignores keys it doesn't use):
  branch_id   - int, restrict to one branch
  date_from   - date, inclusive
  date_to     - date, inclusive
  status      - str, e.g. "OVERDUE" for the due-based reports
"""
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

_HEADER_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2",
                           fill_type="solid")
_SECTION_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2",
                            fill_type="solid")


def _new_workbook(title: str, sheet_name: str):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append([title])
    ws["A1"].font = Font(size=14, bold=True)
    ws.append([])
    return wb, ws


def _style_header_row(ws, row_num: int, ncols: int):
    for c in range(1, ncols + 1):
        ws.cell(row_num, c).font = Font(bold=True)
        ws.cell(row_num, c).fill = _HEADER_FILL


def _autosize(ws, headers: list):
    for i, label in enumerate(headers, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = max(
            12, len(str(label)) + 2)


def _to_bytes(wb) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Vehicle Register Details ────────────────────────────────────────────────

def generate_vehicle_register_xlsx(filters: dict = None, user=None):
    from app.modules.master_data.vehicle.report_service import (
        VehicleRegisterReportService)
    filters = filters or {}
    groups = VehicleRegisterReportService().get_grouped(user=user)
    if filters.get("branch_id"):
        groups = [g for g in groups
                 if str(g["branch_code"]) == str(filters["branch_id"])
                 or any(v.get("branch_code") == filters["branch_id"]
                       for v in g["vehicles"])]

    columns = [
        ("Plate No.", "plate_number"), ("Assignee", "assignee"),
        ("Make", "make"), ("Model", "model"), ("Variant", "variant"),
        ("Year", "year"), ("Displacement", "displacement"),
        ("Fuel", "fuel"), ("Transmission", "transmission"),
        ("BodyColor", "body_color"), ("Type", "type"),
        ("FARNumber", "far_number"), ("MVFileNo", "mv_file_number"),
        ("CRNumber", "cr_number"), ("EngineNumber", "engine_number"),
        ("ChassisNumber", "chassis_number"),
    ]
    wb, ws = _new_workbook("Vehicle Register Details", "Vehicle Register")
    for group in groups:
        ws.append([group["branch_code"]])
        r = ws.max_row
        ws.cell(r, 1).font = Font(bold=True)
        for c in range(1, len(columns) + 1):
            ws.cell(r, c).fill = _SECTION_FILL
        ws.append([label for label, _ in columns])
        _style_header_row(ws, ws.max_row, len(columns))
        for row in group["vehicles"]:
            ws.append([row[key] for _, key in columns])
        ws.append([])
    _autosize(ws, [c[0] for c in columns])
    return (f"Vehicle_Register_Details_{datetime.now():%Y%m%d}.xlsx",
           _to_bytes(wb))


# ── PMS Compliance / Due Report ─────────────────────────────────────────────

def generate_pms_compliance_xlsx(filters: dict = None, user=None):
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    filters = filters or {}
    rows = PMDueCalculationService().get_all_due_vehicles()

    if filters.get("branch_id"):
        rows = [r for r in rows
               if r["vehicle"].branch_id == int(filters["branch_id"])]
    if filters.get("status"):
        rows = [r for r in rows if r["status"] == filters["status"]]

    columns = ["Plate No.", "Branch", "Make", "Model", "Maintenance Type",
              "Next Due (km)", "Current Odometer", "Next Due Date",
              "Status"]
    wb, ws = _new_workbook("PMS Compliance / Due Report", "PMS Compliance")
    ws.append(columns)
    _style_header_row(ws, ws.max_row, len(columns))
    for r in rows:
        v = r["vehicle"]
        ws.append([
            v.plate_number or v.conduction_number, v.branch.name if v.branch else "—",
            v.brand, v.model,
            r["schedule"].maintenance_type.name if r.get("schedule") and r["schedule"].maintenance_type else "—",
            r.get("next_due_km") or "—", v.current_odometer,
            r.get("next_due_date") or "—", r["status"],
        ])
    _autosize(ws, columns)
    return (f"PMS_Compliance_Report_{datetime.now():%Y%m%d}.xlsx",
           _to_bytes(wb))


# ── Vehicle Registration Expiry Report ──────────────────────────────────────

def generate_registration_expiry_xlsx(filters: dict = None, user=None):
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)
    filters = filters or {}
    rows = RegistrationDueCalculationService().get_all_due_vehicles()

    if filters.get("branch_id"):
        rows = [r for r in rows
               if r["vehicle"].branch_id == int(filters["branch_id"])]
    if filters.get("status"):
        rows = [r for r in rows if r["status"] == filters["status"]]

    columns = ["Plate No.", "Branch", "Make", "Model", "Next Due Date",
              "Status"]
    wb, ws = _new_workbook("Vehicle Registration Expiry Report",
                           "Registration Expiry")
    ws.append(columns)
    _style_header_row(ws, ws.max_row, len(columns))
    for r in rows:
        v = r["vehicle"]
        ws.append([
            v.plate_number or v.conduction_number, v.branch.name if v.branch else "—",
            v.brand, v.model, r.get("next_due_date") or "—", r["status"],
        ])
    _autosize(ws, columns)
    return (f"Registration_Expiry_Report_{datetime.now():%Y%m%d}.xlsx",
           _to_bytes(wb))


# ── Maintenance Cost Summary ─────────────────────────────────────────────────

def generate_maintenance_cost_summary_xlsx(filters: dict = None, user=None):
    from app.extensions import db
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)

    filters = filters or {}
    query = MaintenanceOrder.query.filter_by(status="COMPLETED")
    if filters.get("branch_id"):
        query = query.join(MaintenanceOrder.vehicle).filter(
            MaintenanceOrder.vehicle.has(branch_id=int(filters["branch_id"])))
    if filters.get("date_from"):
        query = query.filter(MaintenanceOrder.completed_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(MaintenanceOrder.completed_date <= filters["date_to"])

    orders = query.order_by(MaintenanceOrder.completed_date.desc()).all()
    if user is not None:
        scope_svc = UserOrgScopeService()
        orders = [o for o in orders
                 if scope_svc.covers(user.id, branch_id=o.vehicle.branch_id)]

    columns = ["MO Number", "Vehicle", "Branch", "Category",
              "Maintenance Type", "Completed Date", "Actual Cost"]
    wb, ws = _new_workbook("Maintenance Cost Summary", "Cost Summary")
    ws.append(columns)
    _style_header_row(ws, ws.max_row, len(columns))
    total = 0
    for o in orders:
        cost = float(o.actual_cost or 0)
        total += cost
        ws.append([
            o.document_number or "(draft)",
            f"{o.vehicle.plate_number or o.vehicle.conduction_number} — {o.vehicle.brand} {o.vehicle.model}",
            o.vehicle.branch.name if o.vehicle.branch else "—",
            o.category or o.order_category,
            o.maintenance_type.name if o.maintenance_type else "—",
            o.completed_date, cost,
        ])
    ws.append([])
    ws.append(["", "", "", "", "", "TOTAL", total])
    ws.cell(ws.max_row, 6).font = Font(bold=True)
    ws.cell(ws.max_row, 7).font = Font(bold=True)
    _autosize(ws, columns)
    return (f"Maintenance_Cost_Summary_{datetime.now():%Y%m%d}.xlsx",
           _to_bytes(wb))


# Registry used by both manual export routes and the scheduled emailer.
REPORT_GENERATORS = {
    "RPT_VEHICLE_REGISTER": generate_vehicle_register_xlsx,
    "RPT_PMS_COMPLIANCE": generate_pms_compliance_xlsx,
    "RPT_REGISTRATION_EXPIRY": generate_registration_expiry_xlsx,
    "RPT_MAINTENANCE_COST_SUMMARY": generate_maintenance_cost_summary_xlsx,
}
