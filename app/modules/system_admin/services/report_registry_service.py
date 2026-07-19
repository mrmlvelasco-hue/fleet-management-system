"""Report Registry — turns ReportConfig rows into a discoverable,
permission-filtered catalog of reports, so new reports can be surfaced to
users by adding a ReportConfig row (admin screen) rather than changing code.

Two kinds of report are supported by the same registry:

  * PRINT  — an existing HTML print view (the 5 already shipped: Trip Ticket,
             ATD, Vehicle Movement, Maintenance Order, PM Work Order). These
             map to an existing Flask endpoint that renders a printable page
             the browser exports to PDF.
  * LIST   — a list/summary report exported to Excel/PDF from a registered
             query (the growth path for "add other reports in the app").

A ReportConfig row carries: report_code, name, description, template_path.
We overload template_path with a small "endpoint:<name>" or "query:<key>"
convention so no schema migration is needed to ship the registry now; a
dedicated column can be added later without breaking existing rows.
"""
from app.modules.system_admin.models import ReportConfig


# Reports the app ships with. Seeded into ReportConfig so they appear in the
# unified Reports list; admins can add more rows for new reports.
BUILTIN_REPORTS = [
    {
        "report_code": "RPT_TRIP_TICKET",
        "name": "Trip Ticket",
        "description": "Printable trip ticket (per document).",
        "template_path": "endpoint:transactions.tripticket_print",
        "permission": "tripticket.print",
        "kind": "PRINT",
        "needs_document": True,
    },
    {
        "report_code": "RPT_ATD",
        "name": "Authority To Drive",
        "description": "Printable Authority To Drive (per document).",
        "template_path": "endpoint:transactions.atd_print",
        "permission": "atd.print",
        "kind": "PRINT",
        "needs_document": True,
    },
    {
        "report_code": "RPT_VEHICLE_MOVEMENT",
        "name": "Vehicle Movement",
        "description": "Printable vehicle movement slip (per document).",
        "template_path": "endpoint:transactions.vehiclemovement_print",
        "permission": "vehiclemovement.print",
        "kind": "PRINT",
        "needs_document": True,
    },
    {
        "report_code": "RPT_MAINTENANCE_ORDER",
        "name": "Maintenance Order",
        "description": "Printable maintenance order / work order.",
        "template_path": "endpoint:transactions.maintenanceorder_print",
        "permission": "maintenanceorder.print",
        "kind": "PRINT",
        "needs_document": True,
    },
    {
        "report_code": "RPT_PM_WORK_ORDER",
        "name": "PM Work Order (Dynamic)",
        "description": "Dynamic PM work order report with parameter tokens.",
        "template_path": "endpoint:transactions.maintenanceorder_print",
        "permission": "maintenanceorder.print",
        "kind": "PRINT",
        "needs_document": True,
    },
    {
        "report_code": "RPT_VEHICLE_REGISTER",
        "name": "Vehicle Register Details",
        "description": "All vehicles grouped by branch, with registration "
                       "document numbers (FAR/MV File/CR/Engine/Chassis) — "
                       "printable and Excel-exportable.",
        "template_path": "endpoint:master_data.vehicle_register_report",
        "permission": "vehicle.view",
        "kind": "LIST",
        "needs_document": False,
    },
]


class ReportRegistryService:

    def seed_builtin(self) -> None:
        """Insert built-in report definitions if absent. Idempotent."""
        from app.extensions import db
        for r in BUILTIN_REPORTS:
            if not ReportConfig.query.filter_by(
                    report_code=r["report_code"]).first():
                db.session.add(ReportConfig(
                    report_code=r["report_code"], name=r["name"],
                    description=r["description"],
                    template_path=r["template_path"]))
        db.session.flush()

    def list_available(self, user) -> list:
        """Return registered reports the user may run, filtered by the
        permission encoded in BUILTIN_REPORTS (custom rows with no known
        permission are shown to anyone who can view report config)."""
        perms = {r["report_code"]: r for r in BUILTIN_REPORTS}
        out = []
        for cfg in ReportConfig.query.filter_by(is_active=True).order_by(
                ReportConfig.report_code).all():
            meta = perms.get(cfg.report_code, {})
            required = meta.get("permission")
            if required and not user.has_permission(required):
                continue
            out.append({
                "code": cfg.report_code,
                "name": cfg.name,
                "description": cfg.description,
                "kind": meta.get("kind", "LIST"),
                "endpoint": (cfg.template_path or "").replace("endpoint:", "")
                            if (cfg.template_path or "").startswith("endpoint:")
                            else None,
                "needs_document": meta.get("needs_document", False),
            })
        return out
