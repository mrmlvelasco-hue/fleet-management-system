"""Scheduled (Celery beat, daily) auto-generation of Maintenance Orders for
vehicles that are DUE_SOON or OVERDUE per PMDueCalculationService, plus the
pm_due_soon/pm_overdue notification events.

This module exposes a plain function (`auto_generate_due_maintenance_orders`)
so it's directly unit-testable; the Celery task wrapper just calls it on a
schedule (actual celery beat cron config is a deployment concern — see
README). Not duplicating an order for a vehicle that already has one open
(non-COMPLETED/CANCELLED) for the same maintenance type keeps this idempotent
to run daily.
"""
from app.extensions import db
from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.core.celery_app import celery
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)


class _PMNotificationContext:
    """Minimal stand-in for an ApprovalInstance so the existing
    NotificationEngine.dispatch()/_resolve_recipients() can fire ROLE or
    SPECIFIC_USER notifications for PM due/overdue events, which aren't
    themselves approval-workflow events."""
    def __init__(self, document_type_code, reference_table, reference_id):
        self.document_type = type("DT", (), {"code": document_type_code})()
        self.reference_table = reference_table
        self.reference_id = reference_id
        self.submitted_by = None
        self.current_level = 0
        self.approval_path = None


def auto_generate_due_maintenance_orders() -> int:
    """Create DRAFT Maintenance Orders for DUE_SOON/OVERDUE vehicles that
    don't already have an open order for that maintenance type. Returns the
    number of orders created."""
    from app.modules.system_admin.services.notification_engine import (
        NotificationEngine)

    due_service = PMDueCalculationService()
    order_service = MaintenanceOrderService()
    notif_engine = NotificationEngine()
    created = 0

    for entry in due_service.get_all_due_vehicles():
        vehicle = entry["vehicle"]
        schedule = entry["schedule"]
        maintenance_type_id = schedule.maintenance_type_id

        # Notifications fire regardless of generation policy — visibility
        # (dashboard + notification) is independent of whether the system
        # auto-creates a document. This always ran before creating the
        # order; keep it unconditional now that order-creation is
        # conditional.
        event_code = "pm_overdue" if entry["status"] == "OVERDUE" else "pm_due_soon"

        # PMS-3: MANUAL and AUTO_SCHEDULE (the new recommended default)
        # never auto-create a Maintenance Order — only AUTO_MO does, which
        # preserves the system's original (not recommended) behavior for
        # anyone who explicitly wants it.
        if schedule.next_pms_generation != "AUTO_MO":
            context = _PMNotificationContext(
                document_type_code="MO", reference_table="vehicles",
                reference_id=vehicle.id)
            notif_engine.dispatch(event_code, context)
            continue

        existing_open = (MaintenanceOrder.query
                         .filter_by(vehicle_id=vehicle.id,
                                   maintenance_type_id=maintenance_type_id)
                         .filter(MaintenanceOrder.status.notin_(
                             ["COMPLETED", "CANCELLED"]))
                         .first())
        if existing_open:
            continue

        from datetime import date
        scope_template = schedule.scope_templates[0] if schedule.scope_templates else None
        order = order_service.create(
            vehicle_id=vehicle.id, maintenance_type_id=maintenance_type_id,
            pm_schedule_id=schedule.id,
            scope_template_id=scope_template.id if scope_template else None,
            scheduled_date=date.today(),
            odometer_at_service=vehicle.current_odometer, user=None)
        created += 1

        context = _PMNotificationContext(
            document_type_code="MO", reference_table="maintenance_orders",
            reference_id=order.id)
        notif_engine.dispatch(event_code, context)

    return created


@celery.task(name="maintenance_order.auto_generate_due_orders")
def auto_generate_due_orders_task():
    """Celery task wrapper — actual beat schedule configured at deployment."""
    return auto_generate_due_maintenance_orders()
