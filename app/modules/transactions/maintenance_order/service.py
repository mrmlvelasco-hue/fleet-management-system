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

    def create(self, *, vehicle_id, scheduled_date, user,
               order_category="MAINTENANCE", maintenance_type_id=None,
               transaction_type_id=None, scope_template_id=None,
               pm_schedule_id=None, description=None, odometer_at_service=None,
               assigned_mechanic=None, vendor_id=None, estimated_cost=None,
               driver_id=None):
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
            driver_id=driver_id,
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
        order = db.session.get(MaintenanceOrder, order_id)
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

        return order
