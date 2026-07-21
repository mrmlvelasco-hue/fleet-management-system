"""Vehicle Activity History — a single vehicle's full lifecycle timeline
(Acquisition -> PMS/Repair -> Transfers -> Tire/Battery replacements),
a utilization summary rolled up from it, and an Outlet/Custodian
Assignment History reconstructed from the Audit Trail.

Reused by both the Vehicle Profile print (a single vehicle) and the
standalone Vehicle Activity History Report (one or many vehicles).

The Outlet Assignment History is the one part with no dedicated
transaction table to read from -- Vehicle.branch_id and
Vehicle.assigned_driver_id only ever hold the CURRENT value. Rather than
add new tracking tables, this reconstructs the history from the
already-existing generic Audit Trail (every field change on every model
is logged automatically), which is exactly what an audit trail is for.
"""
from datetime import date
from decimal import Decimal


_TRANSFER_CODES = ("DEP-RELOCATION", "DEP-TRANSFER")


class VehicleActivityHistoryService:

    def get_activity_rows(self, vehicle) -> list:
        """One row per lifecycle event, oldest first: Acquisition, every
        COMPLETED Maintenance Order (PMS/Repair/Transfer/Disposal/other),
        and every COMPLETED tire/battery MOUNT."""
        rows = []

        if vehicle.acquisition_date:
            rows.append({
                "date": vehicle.acquisition_date, "activity_type": "Acquisition",
                "outlet": vehicle.branch.name if vehicle.branch else "—",
                "assigned_to": None,
                "description": "Vehicle acquired and registered",
                "cost": vehicle.acquisition_cost, "odometer": 0,
            })

        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        for mo in (MaintenanceOrder.query
                  .filter_by(vehicle_id=vehicle.id, status="COMPLETED").all()):
            rows.append({
                "date": mo.completed_date or mo.scheduled_date,
                "activity_type": self._mo_activity_type(mo),
                "outlet": (mo.destination_branch.name if mo.destination_branch
                          else (vehicle.branch.name if vehicle.branch else "—")),
                "assigned_to": (mo.driver.full_name if mo.driver
                              else (vehicle.assigned_driver.full_name
                                   if vehicle.assigned_driver else None)),
                "description": mo.description or (
                    mo.transaction_type.name if mo.transaction_type
                    else (mo.maintenance_type.name if mo.maintenance_type else "—")),
                "cost": mo.actual_cost, "odometer": mo.odometer_at_service,
            })

        from app.modules.transactions.tire_txn.models import TireTransaction
        for tx in (TireTransaction.query
                  .filter_by(vehicle_id=vehicle.id, status="COMPLETED",
                            action="MOUNT").all()):
            rows.append({
                "date": tx.transaction_date, "activity_type": "Tire Replacement",
                "outlet": vehicle.branch.name if vehicle.branch else "—",
                "assigned_to": None, "description": tx.remarks or "Tire mounted",
                "cost": None, "odometer": tx.odometer_at_service,
            })

        from app.modules.transactions.battery_txn.models import BatteryTransaction
        for tx in (BatteryTransaction.query
                  .filter_by(vehicle_id=vehicle.id, status="COMPLETED",
                            action="MOUNT").all()):
            rows.append({
                "date": tx.transaction_date, "activity_type": "Battery Replacement",
                "outlet": vehicle.branch.name if vehicle.branch else "—",
                "assigned_to": None, "description": tx.remarks or "Battery mounted",
                "cost": None, "odometer": tx.odometer_at_service,
            })

        rows = [r for r in rows if r["date"] is not None]
        rows.sort(key=lambda r: r["date"])
        return rows

    def _mo_activity_type(self, mo) -> str:
        if mo.order_category == "MAINTENANCE":
            return "PMS" if (mo.category or "") in ("PM", "PREVENTIVE") else "Repair (ATR)"
        tt = mo.transaction_type
        if tt is None:
            return "Operational"
        if tt.code in _TRANSFER_CODES:
            return "Transfer"
        if tt.code.startswith("DIS-"):
            return "Disposal"
        return tt.name

    def get_utilization_summary(self, vehicle, activity_rows) -> dict:
        transfers = [r for r in activity_rows if r["activity_type"] == "Transfer"]
        pms = [r for r in activity_rows if r["activity_type"] == "PMS"]
        repairs = [r for r in activity_rows if r["activity_type"] == "Repair (ATR)"]
        tires = [r for r in activity_rows if r["activity_type"] == "Tire Replacement"]
        batteries = [r for r in activity_rows if r["activity_type"] == "Battery Replacement"]
        maintenance_cost = sum(
            (Decimal(str(r["cost"])) for r in activity_rows
            if r["cost"] is not None and r["activity_type"] in
            ("PMS", "Repair (ATR)", "Tire Replacement", "Battery Replacement")),
            Decimal("0"))

        outlets = {r["outlet"] for r in activity_rows if r["outlet"] and r["outlet"] != "—"}
        vehicle_age_years = None
        if vehicle.acquisition_date:
            today = date.today()
            vehicle_age_years = today.year - vehicle.acquisition_date.year - (
                1 if (today.month, today.day) < (vehicle.acquisition_date.month,
                                                  vehicle.acquisition_date.day)
                else 0)

        return {
            "total_transfers": len(transfers), "pms_count": len(pms),
            "repair_count": len(repairs), "tire_replacements": len(tires),
            "battery_replacements": len(batteries),
            "total_maintenance_cost": maintenance_cost,
            "assigned_outlets_count": len(outlets) or (1 if vehicle.branch else 0),
            "vehicle_age_years": vehicle_age_years,
            "current_odometer": vehicle.current_odometer,
        }

    def get_outlet_history(self, vehicle) -> list:
        """Reconstructed from the Audit Trail -- Vehicle only ever stores
        its CURRENT branch_id/assigned_driver_id, so this replays every
        historical change to build a from/to timeline, rather than
        requiring a new dedicated tracking table."""
        from app.core.models.audit_log import AuditLog
        from app.modules.master_data.org.models import Branch
        from app.modules.master_data.driver.models import Driver
        from app.extensions import db

        logs = (AuditLog.query
               .filter_by(table_name="vehicles", record_id=vehicle.id)
               .order_by(AuditLog.timestamp.asc()).all())

        branch_changes = []   # list of (date, branch_id)
        driver_changes = []   # list of (date, driver_id)
        created_date = vehicle.created_at.date() if vehicle.created_at else None

        for log in logs:
            ts = log.timestamp.date() if log.timestamp else created_date
            if log.action == "CREATE" and log.new_values:
                if "branch_id" in log.new_values:
                    branch_changes.append((ts, log.new_values["branch_id"]))
                if "assigned_driver_id" in log.new_values:
                    driver_changes.append((ts, log.new_values["assigned_driver_id"]))
            elif log.action == "UPDATE" and log.new_values:
                if "branch_id" in log.new_values:
                    branch_changes.append((ts, log.new_values["branch_id"]))
                if "assigned_driver_id" in log.new_values:
                    driver_changes.append((ts, log.new_values["assigned_driver_id"]))

        if not branch_changes:
            # No audit history at all (e.g. a very old record from before
            # auditing was added) -- just show the current branch as the
            # one and only segment.
            return [{
                "from_date": created_date or vehicle.acquisition_date,
                "to_date": None, "outlet": vehicle.branch.name if vehicle.branch else "—",
                "custodian": (vehicle.assigned_driver.full_name
                            if vehicle.assigned_driver else "—"),
            }]

        def _driver_name_as_of(as_of_date):
            active_id = None
            for d, driver_id in driver_changes:
                if d <= as_of_date:
                    active_id = driver_id
                else:
                    break
            if active_id is None:
                return "—"
            drv = db.session.get(Driver, active_id)
            return drv.full_name if drv else "—"

        def _branch_name(branch_id):
            if branch_id is None:
                return "—"
            b = db.session.get(Branch, branch_id)
            return b.name if b else "—"

        segments = []
        for i, (from_date, branch_id) in enumerate(branch_changes):
            to_date = (branch_changes[i + 1][0]
                      if i + 1 < len(branch_changes) else None)
            segments.append({
                "from_date": from_date, "to_date": to_date,
                "outlet": _branch_name(branch_id),
                "custodian": _driver_name_as_of(from_date),
            })
        return segments
