"""Maintenance Order service: creation (with checklist generation from a
PM Scope Template), the shared submit/approve/reject/return/cancel
lifecycle, plus start_work/complete physical-lifecycle actions.

Preventive orders require every checklist item completed before the order
can be marked COMPLETED; Corrective orders have no checklist requirement
(unscheduled/reactive repair work)."""
from datetime import datetime, timezone

from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.maintenance_order.models import (
    MaintenanceOrder, MaintenanceChecklistItem, TransactionType)
from app.modules.master_data.reference.models import MaintenanceType
from app.modules.maintenance_config.models import PMScopeTemplate


class IncompleteChecklistError(Exception):
    pass


class InvalidOrderStateError(Exception):
    pass


class InvalidOrderCategoryError(Exception):
    pass


class TransactionTypeService:
    def create(self, *, code, name, order_category, group=None, sort_order=0):
        tt = TransactionType(code=code, name=name, order_category=order_category,
                             group=group, sort_order=sort_order)
        db.session.add(tt)
        db.session.commit()
        return tt

    def get_by_id(self, tt_id):
        return db.session.get(TransactionType, tt_id)

    def list(self, order_category=None, include_inactive=False):
        q = TransactionType.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if order_category:
            q = q.filter_by(order_category=order_category)
        return q.order_by(TransactionType.group, TransactionType.sort_order).all()

    def deactivate(self, tt_id):
        tt = self.get_by_id(tt_id)
        if tt:
            tt.is_active = False
            db.session.commit()

    def reactivate(self, tt_id):
        tt = self.get_by_id(tt_id)
        if tt:
            tt.is_active = True
            db.session.commit()


class MaintenanceOrderService(BaseTransactionService):
    model = MaintenanceOrder
    document_type_code = "MO"
    reference_table = "maintenance_orders"
    # Matches exactly what maintenanceorder_list.html accesses per row
    # (o.vehicle, o.maintenance_type, o.transaction_type) -- without
    # this, a list of N orders triggered up to 3*N extra lazy-load
    # queries just to render the list page.
    list_eager_load = ["vehicle", "maintenance_type", "transaction_type"]

    def create(self, *, vehicle_id, scheduled_date, user,
               order_category="MAINTENANCE", maintenance_type_id=None,
               transaction_type_id=None, scope_template_id=None,
               pm_schedule_id=None, description=None, odometer_at_service=None,
               assigned_mechanic=None, vendor_id=None, estimated_cost=None,
               driver_id=None, destination_branch_id=None,
               disposal_value=None, disposal_recipient=None,
               assignment_classification=None):
        from app.modules.master_data.vehicle.models import Vehicle
        vehicle = db.session.get(Vehicle, vehicle_id)
        # A vehicle must not have two PM orders open at once: if a PMS is
        # already DRAFT / awaiting approval / IN_PROGRESS, a second one
        # for the same maintenance type would double-book the workshop
        # and double-count the service against the PM schedule. The
        # auto-generation task already refused to create a duplicate for
        # exactly this reason, but the MANUAL create path had no such
        # guard, so the same vehicle could be queued twice by hand.
        if order_category == "MAINTENANCE" and maintenance_type_id:
            existing_open = (MaintenanceOrder.query
                            .filter_by(vehicle_id=vehicle_id,
                                      maintenance_type_id=maintenance_type_id)
                            .filter(MaintenanceOrder.status.notin_(
                                ["COMPLETED", "CANCELLED"]))
                            .first())
            if existing_open:
                raise InvalidOrderStateError(
                    f"This vehicle already has an open "
                    f"{existing_open.maintenance_type.name if existing_open.maintenance_type else 'maintenance'} "
                    f"order ({existing_open.document_number or 'draft'}, "
                    f"status {existing_open.status}). Complete or cancel it "
                    f"before creating another one.")
        # Snapshot the vehicle's CURRENT branch as the "From" for the
        # Asset Transfer Report -- must be captured now, at creation,
        # since completion will update Vehicle.branch_id to the
        # destination, and by then the vehicle's current branch would
        # already be the new one, not the "From" branch being reported.
        origin_branch_id = (vehicle.branch_id if destination_branch_id
                           and vehicle else None)
        # Per the spec: "Not every Maintenance Order represents vehicle
        # maintenance. Administrative, Deployment, Disposal, and
        # Accessories are valid Maintenance Orders" — Operational orders
        # are normal work requests and must NOT require a Maintenance
        # Type or PM Scope Template at all. MAINTENANCE-category orders
        # keep the exact original requirement (nothing changes for
        # existing PM/CM callers).
        if order_category == "OPERATIONAL":
            maintenance_type_id = None
            scope_template_id = None
            pm_schedule_id = None
        elif not maintenance_type_id:
            raise InvalidOrderCategoryError(
                "Maintenance Type is required for a Maintenance-category order.")

        if transaction_type_id:
            tt = db.session.get(TransactionType, transaction_type_id)
            if tt and tt.order_category != order_category:
                raise InvalidOrderCategoryError(
                    f"Transaction Type '{tt.name}' belongs to the "
                    f"{tt.order_category} category, not {order_category}.")

        mtype = (db.session.get(MaintenanceType, maintenance_type_id)
                if maintenance_type_id else None)

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        order = MaintenanceOrder(
            document_number=doc_number, vehicle_id=vehicle_id,
            order_category=order_category, transaction_type_id=transaction_type_id,
            maintenance_type_id=maintenance_type_id,
            category=mtype.category if mtype else None,
            pm_schedule_id=pm_schedule_id,
            scope_template_id=scope_template_id, description=description,
            odometer_at_service=odometer_at_service,
            scheduled_date=scheduled_date,
            assigned_mechanic=assigned_mechanic, vendor_id=vendor_id,
            estimated_cost=estimated_cost, status="DRAFT",
            driver_id=driver_id, destination_branch_id=destination_branch_id,
            origin_branch_id=origin_branch_id,
            disposal_value=disposal_value, disposal_recipient=disposal_recipient,
            assignment_classification=assignment_classification,
            requested_by=user.id if user else None)
        db.session.add(order)
        db.session.flush()

        if scope_template_id:
            template = db.session.get(PMScopeTemplate, scope_template_id)
            for item in template.items:
                order.checklist_items.append(MaintenanceChecklistItem(
                    activity_code=item.activity_code,
                    activity_description=item.activity_description,
                    sort_order=item.sort_order))

        db.session.commit()
        return order

    def start_work(self, order_id: int):
        """Move an approved order into IN_PROGRESS.

        The approval gate is enforced HERE, not only in the template:
        the detail page hides the Start Work button until the order is
        approved, but a hidden button is not a control -- a direct POST
        would otherwise let work begin on an unsubmitted or
        still-pending order, skipping the approval step entirely.

        Note that when a document type is configured as NOT requiring
        approval, the Approval Engine still creates an instance and
        marks it APPROVED immediately on submit, so this single check
        works for both approval-required and no-approval setups. What it
        correctly rejects is an order that was never submitted at all.
        """
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")
        if order.status != "DRAFT":
            raise InvalidOrderStateError(
                f"Work can only be started on a DRAFT order "
                f"(this one is {order.status}).")
        instance = order.approval_instance
        if instance is None:
            raise InvalidOrderStateError(
                "This order has not been submitted yet. Submit it for "
                "approval before starting work.")
        if instance.status != "APPROVED":
            raise InvalidOrderStateError(
                f"This order is not approved yet (approval status: "
                f"{instance.status}). Work can only start once it is "
                f"approved.")
        order.status = "IN_PROGRESS"
        db.session.commit()
        return order

    def toggle_checklist_item(self, item_id: int, done: bool, user):
        item = db.session.get(MaintenanceChecklistItem, item_id)
        order = item.order
        if order.status != "IN_PROGRESS":
            raise InvalidOrderStateError(
                "Checklist items can only be updated while the order is "
                "IN_PROGRESS.")
        item.is_done = done
        item.done_by = user.id if done and user else None
        item.done_at = datetime.now(timezone.utc) if done else None
        db.session.commit()
        return item

    def mark_all_checklist_items(self, order_id: int, done: bool, user):
        """Bulk toggle every checklist item on an order in one commit --
        backs the "Mark All Done" button so a long PM scope (dozens of
        lines) doesn't need one click + page round-trip per item."""
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")
        if order.status != "IN_PROGRESS":
            raise InvalidOrderStateError(
                "Checklist items can only be updated while the order is "
                "IN_PROGRESS.")
        now = datetime.now(timezone.utc)
        for item in order.checklist_items:
            item.is_done = done
            item.done_by = user.id if done and user else None
            item.done_at = now if done else None
        db.session.commit()
        return order.checklist_items

    def complete(self, order_id: int, actual_cost, completed_date):
        order = db.session.get(MaintenanceOrder, order_id)
        # "PM" is the current code for Preventive Maintenance (Category
        # Lookup, admin-configurable) — "PREVENTIVE" is kept here too for
        # any row that predates the category-code migration and wasn't
        # translated for some reason, so the rule doesn't silently stop
        # applying to it.
        if order.category in ("PM", "PREVENTIVE") and order.checklist_items:
            incomplete = [i for i in order.checklist_items if not i.is_done]
            if incomplete:
                raise IncompleteChecklistError(
                    f"{len(incomplete)} checklist item(s) still incomplete; "
                    "all items must be done before completing a Preventive "
                    "Maintenance order.")
        order.actual_cost = actual_cost
        order.completed_date = completed_date
        order.status = "COMPLETED"
        if order.odometer_at_service and (
                order.vehicle.current_odometer is None or
                order.odometer_at_service > order.vehicle.current_odometer):
            order.vehicle.current_odometer = order.odometer_at_service
        db.session.commit()

        # "Vehicle Assignment Memo" workflow: an Operational order with a
        # Driver/Assignee set (Assignment, Reassignment, Relocation,
        # Transfer transaction types) updates the vehicle's current
        # assigned driver on completion -- the same outcome an approved
        # ATD produces (see assignment_hooks.py), so either document can
        # be the operative record for a given handover.
        if order.driver_id:
            from app.modules.master_data.vehicle.assignment_hooks import (
                assign_driver_to_vehicle)
            assign_driver_to_vehicle(order.vehicle_id, order.driver_id)

        # "Asset Transfer Report" workflow: an Operational order with a
        # Destination Branch set (Relocation/Transfer transaction types)
        # moves the vehicle to that branch on completion, and generates
        # a dedicated ATR-2026-NNNN reference number the first time it's
        # completed (not regenerated on any later re-save).
        if order.destination_branch_id:
            from app.modules.master_data.vehicle.assignment_hooks import (
                transfer_vehicle_branch)
            transfer_vehicle_branch(order.vehicle_id, order.destination_branch_id)
            if not order.transfer_reference_number:
                from app.core.numbering.numbering_service import (
                    AutoNumberingService)
                order.transfer_reference_number = (
                    AutoNumberingService().generate("ATR"))
                db.session.commit()

        # "Asset Disposal Report" workflow: completing an order whose
        # Transaction Type belongs to the DISPOSAL group retires the
        # vehicle (status -> DISPOSED, already correctly excluded from
        # PM/registration due-calculations and hidden from active lists
        # elsewhere) and generates a dedicated ADR-2026-NNNN reference
        # number, the retirement-stage counterpart to the Asset Transfer
        # Report's ATR No. The disposal REASON/METHOD is the transaction
        # type itself (Scrappage/Carnapped/Total Loss/Uneconomical/Sold/
        # Donated) -- disposal_value/disposal_recipient only add what the
        # transaction type alone can't capture.
        if (order.transaction_type
                and order.transaction_type.group == "DISPOSAL"):
            order.vehicle.status = "DISPOSED"
            if not order.disposal_reference_number:
                from app.core.numbering.numbering_service import (
                    AutoNumberingService)
                order.disposal_reference_number = (
                    AutoNumberingService().generate("ADR"))
            db.session.commit()

        return order
