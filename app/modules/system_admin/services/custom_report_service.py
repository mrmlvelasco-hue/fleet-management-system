"""Custom Report service — saving, running and delivering user-built
reports.

Every run goes through report_builder.run_report(), which re-validates
the saved keys against the curated whitelist. Nothing here trusts the
stored definition to still be valid (a column could have been removed
since it was saved) or to be safe (a row could have been edited directly
in the database).

v315: AND/OR filter logic, group-by, paged runs, CSV as well as Excel,
and a per-report schedule (stored as a ScheduledReport with report code
CUSTOM:<id>, so the existing `flask report run-due` job delivers it and
it appears on the Scheduled Reports screen). Saving from the new React
builder validates the whole definition (strict=True); the older Flask
form keeps its lenient behaviour.
"""
import json

from app.extensions import db
from app.modules.system_admin.models import CustomReport
from app.core.reporting.report_builder import (
    DATA_SOURCES, MAX_ROWS, run_report, to_csv, to_xlsx, ReportBuilderError)

SCHEDULE_PREFIX = "CUSTOM:"
FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY")
EXPORT_FORMATS = ("csv", "xlsx")
_UNSET = object()


def schedule_code(report_id) -> str:
    return f"{SCHEDULE_PREFIX}{report_id}"


class CustomReportService:

    def list(self, include_inactive: bool = False) -> list:
        query = CustomReport.query
        if not include_inactive:
            query = query.filter_by(is_active=True)
        return query.order_by(CustomReport.name.asc()).all()

    def get_by_id(self, report_id: int):
        return db.session.get(CustomReport, report_id)

    def create(self, *, name, data_source, fields, filters=None,
               sort_key=None, sort_dir="asc", description=None,
               row_limit=1000, default_recipients=None, user=None,
               filter_logic="AND", group_by=None, strict=False):
        self._validate(name, data_source, fields)
        definition = dict(fields=list(fields), filters=filters or [],
                          sort_key=sort_key or None,
                          sort_dir=sort_dir or "asc",
                          filter_logic=_logic(filter_logic),
                          group_by=group_by or None)
        if strict:
            self._check_runs(data_source, definition, user)
        report = CustomReport(
            name=name.strip(), description=(description or "").strip() or None,
            data_source=data_source,
            fields_json=json.dumps(definition["fields"]),
            filters_json=json.dumps(definition["filters"]),
            sort_key=definition["sort_key"],
            sort_dir=definition["sort_dir"],
            filter_logic=definition["filter_logic"],
            group_by=definition["group_by"],
            row_limit=_row_limit(row_limit),
            default_recipients=(default_recipients or "").strip() or None,
            created_by=user.id if user else None)
        db.session.add(report)
        db.session.commit()
        return report

    def update(self, report_id, *, name, data_source, fields, filters=None,
               sort_key=None, sort_dir="asc", description=None,
               row_limit=1000, default_recipients=None, user=None,
               filter_logic=_UNSET, group_by=_UNSET, strict=False):
        """filter_logic / group_by left out (the Flask form does not send
        them) keep their saved values instead of being reset."""
        report = self.get_by_id(report_id)
        if report is None:
            raise ReportBuilderError("Report not found.")
        self._validate(name, data_source, fields)
        logic = (report.filter_logic or "AND") if filter_logic is _UNSET \
            else _logic(filter_logic)
        group = report.group_by if group_by is _UNSET else (group_by or None)
        if data_source != report.data_source and group_by is _UNSET:
            group = None   # a group key from another source is meaningless
        if strict:
            self._check_runs(data_source, dict(
                fields=list(fields), filters=filters or [],
                sort_key=sort_key or None, sort_dir=sort_dir or "asc",
                filter_logic=logic, group_by=group), user)
        report.name = name.strip()
        report.description = (description or "").strip() or None
        report.data_source = data_source
        report.fields_json = json.dumps(list(fields))
        report.filters_json = json.dumps(filters or [])
        report.sort_key = sort_key or None
        report.sort_dir = sort_dir or "asc"
        report.filter_logic = logic
        report.group_by = group
        report.row_limit = _row_limit(row_limit)
        report.default_recipients = (default_recipients or "").strip() or None
        report.updated_by = user.id if user else None
        db.session.commit()
        return report

    def deactivate(self, report_id):
        report = self.get_by_id(report_id)
        if report:
            report.is_active = False
            sched = self.schedule_for(report.id)
            if sched is not None:
                sched.is_active = False
            db.session.commit()

    def _validate(self, name, data_source, fields):
        if not (name or "").strip():
            raise ReportBuilderError("Give the report a name.")
        if data_source not in DATA_SOURCES:
            raise ReportBuilderError(f"Unknown data source '{data_source}'.")
        source = DATA_SOURCES[data_source]
        valid = [f for f in (fields or []) if f in source.fields]
        if not valid:
            raise ReportBuilderError("Select at least one column.")

    def _check_runs(self, data_source, d, user):
        """Run the definition for one row, so every key, operator, value
        and permission problem is reported at SAVE time rather than the
        first time someone opens the report."""
        run_report(data_source, d["fields"], filters=d["filters"],
                   sort_key=d["sort_key"], sort_dir=d["sort_dir"],
                   filter_logic=d["filter_logic"], group_by=d["group_by"],
                   page=1, per_page=1, user=user, strict=True)

    def run(self, report, user=None, page=None, per_page=None) -> dict:
        """Execute a saved report. The permission check lives in
        run_report(), keyed off the data source, so a saved report can
        never expose data the person couldn't otherwise see."""
        return run_report(
            report.data_source, report.fields, filters=report.filters,
            sort_key=report.sort_key, sort_dir=report.sort_dir,
            limit=report.row_limit, user=user,
            filter_logic=report.filter_logic or "AND",
            group_by=report.group_by, page=page, per_page=per_page)

    def file_name(self, report, fmt) -> str:
        safe_name = "".join(
            ch for ch in report.name if ch.isalnum() or ch in " -_").strip()
        return f"{safe_name or 'Custom_Report'}.{fmt}"

    def to_file(self, report, fmt="xlsx", user=None) -> tuple:
        """(filename, bytes, mimetype) for CSV or Excel."""
        fmt = (fmt or "xlsx").lower()
        if fmt not in EXPORT_FORMATS:
            raise ReportBuilderError(f"Unknown export format '{fmt}'.")
        result = self.run(report, user=user)
        if fmt == "csv":
            return (self.file_name(report, "csv"), to_csv(result),
                    "text/csv")
        return (self.file_name(report, "xlsx"),
                to_xlsx(result, title=report.name),
                "application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet")

    def to_excel(self, report, user=None) -> tuple:
        filename, data, _ = self.to_file(report, "xlsx", user=user)
        return filename, data

    def email(self, report, recipients=None, user=None) -> dict:
        """Queue the report for delivery as an Excel attachment.

        Goes through the EmailOutbox rather than sending inline, for the
        same reason notification email does: the person clicking "Email"
        gets an immediate response instead of waiting on SMTP. The
        workbook is generated by the DRAIN at send time (see
        EmailOutboxService), not stored here -- that keeps potentially
        large spreadsheets out of the database, and means a report
        delivered on a schedule carries current data rather than a
        snapshot from whenever the schedule was set up.
        """
        addresses = _addresses(recipients or report.default_recipients)
        if not addresses:
            raise ReportBuilderError(
                "No recipients given, and this report has no default "
                "recipients saved.")
        # Fail fast: if the definition can't run, say so now rather than
        # silently queueing mail that will fail later out of sight.
        self.run(report, user=user)

        from app.modules.system_admin.models import EmailOutbox
        for address in addresses:
            db.session.add(EmailOutbox(
                to_email=address,
                to_user_id=user.id if user else None,
                subject=f"[FMS] {report.name}",
                body_html=(
                    f"<p>Attached is the <strong>{report.name}</strong> "
                    f"report from the Fleet Management System.</p>"
                    f"<p>{report.description or ''}</p>"),
                event_code="CUSTOM_REPORT",
                reference_table="custom_reports",
                reference_id=report.id,
                status="PENDING"))
        db.session.commit()
        return {"queued": len(addresses), "recipients": addresses}

    # ── schedule ────────────────────────────────────────────────────

    def schedule_for(self, report_id):
        from app.modules.system_admin.models import ScheduledReport
        return (ScheduledReport.query
                .filter_by(report_code=schedule_code(report_id),
                           is_active=True)
                .order_by(ScheduledReport.id.desc()).first())

    def set_schedule(self, report, *, frequency, recipients=None,
                     export_format="csv", user=None):
        """Create, change or (frequency NONE/empty) stop the report's
        recurring delivery. The delivery runs AS the person who set it,
        so it can never send more than they could see themselves."""
        from app.modules.system_admin.models import ScheduledReport
        frequency = (frequency or "").upper()
        current = self.schedule_for(report.id)
        if frequency in ("", "NONE"):
            if current is not None:
                current.is_active = False
                db.session.commit()
            return None
        if frequency not in FREQUENCIES:
            raise ReportBuilderError(
                f"Schedule must be one of {', '.join(FREQUENCIES)}.")
        fmt = (export_format or "csv").lower()
        if fmt not in EXPORT_FORMATS:
            raise ReportBuilderError("Export format must be CSV or XLSX.")
        addresses = _addresses(recipients or report.default_recipients)
        if not addresses:
            raise ReportBuilderError(
                "Add at least one email recipient for the schedule.")
        bad = [a for a in addresses if "@" not in a or " " in a]
        if bad:
            raise ReportBuilderError(
                f"Not a valid email address: {', '.join(bad)}.")
        options = json.dumps({"format": fmt,
                              "run_as": user.id if user else None})
        if current is None:
            current = ScheduledReport(report_code=schedule_code(report.id))
            db.session.add(current)
        changed = current.frequency != frequency or current.id is None
        current.name = report.name
        current.frequency = frequency
        current.recipients = ", ".join(addresses)
        current.filters_json = options
        if changed or current.next_run_at is None:
            current.next_run_at = current.compute_next_run()
        db.session.commit()
        return current

    def scheduled_file(self, schedule) -> tuple:
        """(filename, bytes) for a CUSTOM:<id> schedule, run as the user
        who set it. Used by ScheduledReportService.run_due()."""
        from app.modules.user_management.models import User
        try:
            report_id = int(schedule.report_code[len(SCHEDULE_PREFIX):])
        except ValueError:
            raise ReportBuilderError(
                f"Bad schedule code '{schedule.report_code}'.") from None
        report = self.get_by_id(report_id)
        if report is None or not report.is_active:
            raise ReportBuilderError(
                f"Custom report {report_id} no longer exists.")
        opts = schedule.filters_dict()
        user = None
        if opts.get("run_as"):
            user = db.session.get(User, opts["run_as"])
            if user is None or not user.is_active:
                raise ReportBuilderError(
                    "The person who scheduled this report is no longer an "
                    "active user; set the schedule again.")
        filename, data, _ = self.to_file(report, opts.get("format", "xlsx"),
                                         user=user)
        return filename, data


def _logic(value):
    return "OR" if str(value or "AND").upper() == "OR" else "AND"


def _row_limit(value):
    try:
        n = int(value or 1000)
    except (TypeError, ValueError):
        raise ReportBuilderError("Max rows must be a whole number.") from None
    return max(1, min(n, MAX_ROWS))


def _addresses(text):
    return [a.strip() for a in (text or "").replace(";", ",").split(",")
            if a.strip()]
