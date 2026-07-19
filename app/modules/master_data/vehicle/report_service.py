"""Vehicle Register Details report — reproduces the legacy VEMS "Vehicle
Register Details" layout: one section per branch, one row per vehicle,
with plate/assignee/spec/registration-document columns. Reuses
VehicleService.list() so org-scope filtering matches the master-data list
page exactly (a branch-scoped user sees only their own branch's section).
"""
from app.modules.master_data.vehicle.service import VehicleService


class VehicleRegisterReportService:

    def get_rows(self, user=None, include_inactive=False) -> list:
        """One dict per vehicle, already carrying the display fields the
        report needs (avoids re-querying relationships per row in the
        template)."""
        vehicles = VehicleService().list(
            include_inactive=include_inactive, user=user)
        rows = []
        for v in vehicles:
            rows.append({
                "branch_code": v.branch.code if v.branch else "—",
                "branch_name": v.branch.name if v.branch else "Unassigned",
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
        """Rows grouped by branch, sorted by branch code then plate number
        — matches the legacy report's section-per-branch layout."""
        rows = self.get_rows(user=user, include_inactive=include_inactive)
        by_branch = {}
        for r in rows:
            by_branch.setdefault(r["branch_code"], {
                "branch_code": r["branch_code"],
                "branch_name": r["branch_name"],
                "vehicles": []})["vehicles"].append(r)
        groups = list(by_branch.values())
        groups.sort(key=lambda g: g["branch_code"])
        for g in groups:
            g["vehicles"].sort(key=lambda r: r["plate_number"] or "")
        return groups
