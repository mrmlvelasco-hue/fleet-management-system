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
    MaintenanceOrder, MaintenanceChecklistItem)
from app.modules.master_data.reference.models import MaintenanceType
from app.modules.maintenance_config.models import PMScopeTemplate


class IncompleteChecklistError(Exception):
    pass


class InvalidOrderStateError(Exception):
    pass


class MaintenanceOrderService(BaseTransactionService):
    model = MaintenanceOrder
    document_type_code = "MO"
    reference_table = "maintenance_orders"

    def create(self, *, vehicle_id, maintenance_type_id, scheduled_date,
               user, scope_template_id=None, pm_schedule_id=None,
               description=None, odometer_at_service=None,
               assigned_mechanic=None, vendor_id=None, estimated_cost=None):
        mtype = db.session.get(MaintenanceType, maintenance_type_id)

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        order = MaintenanceOrder(
            document_number=doc_number, vehicle_id=vehicle_id,
            maintenance_type_id=maintenance_type_id,
            category=mtype.category, pm_schedule_id=pm_schedule_id,
            scope_template_id=scope_template_id, description=description,
            odometer_at_service=odometer_at_service,
            scheduled_date=scheduled_date,
            assigned_mechanic=assigned_mechanic, vendor_id=vendor_id,
            estimated_cost=estimated_cost, status="DRAFT",
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

    def complete(self, order_id: int, actual_cost, completed_date):
        order = db.session.get(MaintenanceOrder, order_id)
        if order.category == "PREVENTIVE" and order.checklist_items:
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
        return order
