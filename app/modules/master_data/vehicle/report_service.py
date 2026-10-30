"""Vehicle Register Details report — one section per COMPANY, one row per
vehicle,
with plate/assignee/spec/registration-document columns. Reuses
VehicleService.list() so org-scope filtering matches the master-data list
page exactly (a branch-scoped user sees only their own branch's section).
"""
from app.extensions import db
from app.modules.master_data.vehicle.service import VehicleService


class VehicleRegisterReportService:

    def get_rows(self, user=None, include_inactive=False) -> list:
        """One dict per vehicle, already carrying the display fields the
        report needs (avoids re-querying relationships per row in the
        template). Always includes disposed vehicles -- unlike the
        active-fleet Vehicle Master list, this report is meant to be a
        complete register/audit trail, so a retired vehicle shouldn't
        just disappear from it."""
        vehicles = VehicleService().list(
            include_inactive=include_inactive, user=user, include_disposed=True)
        from app.core.company_resolution import effective_company_id
        from app.modules.master_data.org.models import OrgCompany

        rows = []
        for v in vehicles:
            # Effective Company: the vehicle's own, else its branch's --
            # the one shared definition, so this report cannot disagree
            # with the list or the dashboard about what a vehicle
            # belongs to.
            _co_id = effective_company_id(v)
            _co = db.session.get(OrgCompany, _co_id) if _co_id else None
            rows.append({
                # Carried so callers can filter by branch without
                # matching on a display string. branch_code is "—" for an
                # unassigned vehicle, and two branches could legitimately
                # share a name; neither is safe to filter on.
                "branch_id": v.branch.id if v.branch else None,
                "branch_code": v.branch.code if v.branch else "—",
                "branch_name": v.branch.name if v.branch else "Unassigned",
                # Stage 3C: the report groups on Company now. Region and
                # Area/District are RESOLVED from the Company record and
                # never stored on the row, the same rule Stage 3A set for
                # the vehicle itself, so they cannot drift from the
                # master. company_id is carried so callers can filter
                # without matching on a display string -- per D2 several
                # Companies share one short_name.
                "company_id": _co.id if _co else None,
                "company": _co.short_name if _co else "Unassigned",
                "company_name": (_co.company_name or _co.short_name)
                                if _co else "Unassigned",
                "region": (_co.region.name
                           if _co and _co.region else "—"),
                "area_district": (_co.area_district.name
                                  if _co and _co.area_district else "—"),
                "plate_number": v.plate_number or v.conduction_number or "—",
                "assignee": (v.assigned_driver.full_name
                            if v.assigned_driver else "—"),
                "make": v.brand,
                "model": v.model,
                "variant": v.variant or "—",
                "year": v.year,
                "displacement": v.displacement or "—",
                "fuel": v.fuel_type or "—",
                "transmission": v.transmission or "—",
                "body_color": v.color or "—",
                "type": v.vehicle_type.name if v.vehicle_type else "—",
                "far_number": v.far_number or "—",
                "mv_file_number": v.mv_file_number or "—",
                "cr_number": v.cr_number or "—",
                "engine_number": v.engine_number or "—",
                "chassis_number": v.chassis_number or "—",
            })
        return rows

    def get_grouped(self, user=None, include_inactive=False) -> list:
        """Rows grouped by COMPANY, sorted by company label then plate.

        Grouped on company_id rather than the name: per D2 several
        Companies legitimately share one short_name and differ only by
        Region + Area/District, so grouping by name would merge two
        distinct sections into one.

        A vehicle whose Company cannot be resolved keeps its own
        "Unassigned" section rather than being dropped -- this report is
        a complete register, and one that quietly loses vehicles is
        worse than one that shows a gap.
        """
        rows = self.get_rows(user=user, include_inactive=include_inactive)
        by_company = {}
        for r in rows:
            key = r["company_id"]
            label = ("Unassigned" if not key else
                     f"{r['company']} ({r['region']} / {r['area_district']})")
            by_company.setdefault(key, {
                "company_id": key,
                "company": r["company"],
                "company_name": r["company_name"],
                "company_label": label,
                "region": r["region"],
                "area_district": r["area_district"],
                "vehicles": []})["vehicles"].append(r)
        groups = list(by_company.values())
        # Unassigned last: it is the exception list, not a company.
        groups.sort(key=lambda g: (g["company_id"] is None,
                                   g["company_label"]))
        for g in groups:
            g["vehicles"].sort(key=lambda r: r["plate_number"] or "")
        return groups
