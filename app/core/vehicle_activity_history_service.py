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
                # No source document: the acquisition is the vehicle
                # record itself. Still carries the keys, so a caller can
                # read every row the same way.
                "document_type": "Vehicle", "document_id": vehicle.id,
                "document_number": (vehicle.plate_number
                                    or vehicle.conduction_number),
                "reference_label": None, "reference_number": None,
            })

        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        for mo in (MaintenanceOrder.query
                  .filter_by(vehicle_id=vehicle.id, status="COMPLETED").all()):
            rows.append({
                "date": mo.completed_date or mo.scheduled_date,
                "activity_type": self.activity_type(mo),
                "outlet": (mo.destination_branch.name if mo.destination_branch
                          else (vehicle.branch.name if vehicle.branch else "—")),
                "assigned_to": (mo.driver.full_name if mo.driver
                              else (vehicle.assigned_driver.full_name
                                   if vehicle.assigned_driver else None)),
                "description": self._mo_description(mo),
                "cost": mo.actual_cost, "odometer": mo.odometer_at_service,
                # Which record is this? A retirement or a branch move is
                # the end of the asset's story here, so the order that
                # caused it has to be nameable from the timeline itself
                # (Michael, 2026-10-13) rather than hunted for in the
                # Maintenance module.
                "document_type": "Maintenance Order",
                "document_id": mo.id,
                "document_number": mo.document_number,
                "reference_label": self.reference_label(mo),
                "reference_number": self.reference_number(mo),
            })

        # Purchase Requests raised off this vehicle's orders.
        #
        # Reached by walking the ORDER, because the link lives there
        # (maintenance_orders.purchase_request_id) -- PurchaseRequest
        # itself has no vehicle_id at all. That covers every PR the
        # auto-generation produces, which is the case that matters. A PR
        # raised standalone has no vehicle anywhere on it and cannot
        # appear here until one is added.
        #
        # Deliberately NOT filtered to COMPLETED orders, unlike every
        # other row above. A PR is created the moment an order is
        # approved, long before the work finishes; holding it back until
        # completion would hide committed money for exactly as long as
        # the job takes, which is precisely when someone is asking where
        # the parts have got to.
        seen_pr_ids = set()
        for mo in (MaintenanceOrder.query
                  .filter(MaintenanceOrder.vehicle_id == vehicle.id,
                          MaintenanceOrder.purchase_request_id.isnot(None))
                  .all()):
            pr = mo.purchase_request
            if pr is None or pr.id in seen_pr_ids:
                continue
            seen_pr_ids.add(pr.id)
            rows.append({
                "date": (pr.created_at.date()
                        if getattr(pr, "created_at", None) is not None
                        else (mo.completed_date or mo.scheduled_date)),
                "activity_type": "Purchase Request",
                "outlet": vehicle.branch.name if vehicle.branch else "—",
                "assigned_to": (pr.requester.full_name
                               if getattr(pr, "requester", None) else None),
                # Both numbers, so "what is this and why was it raised"
                # is answerable straight off the timeline.
                "description": (
                    f"{pr.document_number or '(draft PR)'} — "
                    f"{pr.description or 'Parts procurement'} "
                    f"(from {mo.document_number or 'Maintenance Order'})"),
                "cost": pr.amount,
                "odometer": mo.odometer_at_service,
                "document_type": "Purchase Request", "document_id": pr.id,
                "document_number": pr.document_number,
                "reference_label": None, "reference_number": None,
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
                "document_type": "Tire Transaction", "document_id": tx.id,
                "document_number": getattr(tx, "document_number", None),
                "reference_label": None, "reference_number": None,
            })

        from app.modules.transactions.battery_txn.models import BatteryTransaction
        for tx in (BatteryTransaction.query
                  .filter_by(vehicle_id=vehicle.id, status="COMPLETED",
                            action="MOUNT").all()):
            rows.append({
                "date": tx.transaction_date, "activity_type": "Battery Replacement",
                "outlet": vehicle.branch.name if vehicle.branch else "—",
                "assigned_to": None, "description": tx.remarks or "Battery mounted",
                # Unlike TireTransaction, BatteryTransaction has no
                # odometer_at_service column -- batteries aren't tracked
                # by mileage the way tires are, and no caller anywhere
                # (form, service, API) ever populates one. Reading it
                # unconditionally raised AttributeError for every vehicle
                # with a completed battery mount, which two different
                # callers then hid two different ways: one swallowed it
                # into an empty-looking history, the other let it crash
                # outright (KI-05). getattr(..., None) makes "no reading
                # recorded" the correct, honest value here, same as it
                # would be for any other row type missing one.
                "cost": None, "odometer": getattr(tx, "odometer_at_service", None),
                "document_type": "Battery Transaction", "document_id": tx.id,
                "document_number": getattr(tx, "document_number", None),
                "reference_label": None, "reference_number": None,
            })

        rows = [r for r in rows if r["date"] is not None]
        rows.sort(key=lambda r: r["date"])
        return rows

    @staticmethod
    def reference_label(mo):
        """"ADR No." for a disposal, "ATR No." for a transfer, else None.

        Both numbers are generated on completion (Asset Disposal Report /
        Asset Transfer Report) and are what the paperwork is filed under,
        so the history row shows the number under the name the filing
        clerk knows it by.
        """
        if getattr(mo, "disposal_reference_number", None):
            return "ADR No."
        if getattr(mo, "transfer_reference_number", None):
            return "ATR No."
        return None

    @staticmethod
    def reference_number(mo):
        return (getattr(mo, "disposal_reference_number", None)
                or getattr(mo, "transfer_reference_number", None))

    def _mo_description(self, mo) -> str:
        """What happened, in one line.

        A disposal or a transfer is not self-explanatory from the order's
        own description -- "Unit sold at auction" does not say what it
        was sold for, to whom, or under which ADR -- so the row states
        it. An ordinary maintenance order keeps exactly the description
        it always had.
        """
        base = mo.description or (
            mo.transaction_type.name if mo.transaction_type
            else (mo.maintenance_type.name if mo.maintenance_type else "—"))
        parts = []
        reference = self.reference_number(mo)
        if reference:
            parts.append(f"{self.reference_label(mo)} {reference}")
        if self._is_disposal(mo):
            if mo.transaction_type is not None:
                parts.append(f"Method: {mo.transaction_type.name}")
            if getattr(mo, "disposal_recipient", None):
                parts.append(f"To: {mo.disposal_recipient}")
            if getattr(mo, "disposal_value", None) is not None:
                parts.append(f"Value: {mo.disposal_value:,.2f}")
        elif self._is_transfer(mo):
            origin = (mo.origin_branch.name if getattr(mo, "origin_branch", None)
                      else None)
            destination = (mo.destination_branch.name
                           if getattr(mo, "destination_branch", None) else None)
            if origin or destination:
                parts.append(f"{origin or '—'} → {destination or '—'}")
        return " · ".join([base] + parts) if parts else base

    @staticmethod
    def _is_disposal(mo) -> bool:
        """The transaction type's GROUP, which is what complete() itself
        checks before retiring the vehicle -- not the code prefix, so an
        admin-added disposal type counts too."""
        tt = mo.transaction_type
        return bool(tt is not None and (
            tt.group == "DISPOSAL" or (tt.code or "").startswith("DIS-")))

    @staticmethod
    def _is_transfer(mo) -> bool:
        tt = mo.transaction_type
        return bool(tt is not None and (tt.code in _TRANSFER_CODES
                                        or mo.destination_branch_id))

    def activity_type(self, mo) -> str:
        """PMS / Repair (ATR) / Transfer / Disposal / <type name> for one
        Maintenance Order. Public because the Vehicle detail's
        Maintenance History list labels its rows with the same
        derivation -- two different answers for the same order on the
        same page would be worse than none."""
        if mo.order_category == "MAINTENANCE":
            return "PMS" if (mo.category or "") in ("PM", "PREVENTIVE") else "Repair (ATR)"
        tt = mo.transaction_type
        if tt is None:
            return "Operational"
        # Disposal first: a disposal order with a destination branch set
        # is still a disposal.
        if self._is_disposal(mo):
            return "Disposal"
        if self._is_transfer(mo):
            return "Transfer"
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
