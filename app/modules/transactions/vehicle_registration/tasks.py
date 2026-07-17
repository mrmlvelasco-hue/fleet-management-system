"""Scheduled (Celery beat, daily) auto-generation of Vehicle Registration
renewals for vehicles that are DUE_SOON or OVERDUE per
RegistrationDueCalculationService, plus registration_due_soon/
registration_overdue notification events. Mirrors
maintenance_order/tasks.py's shape exactly.
"""
from app.core.celery_app import celery
from app.modules.transactions.vehicle_registration.models import (
    VehicleRegistration)
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)
from app.modules.registration_config.service import (
    RegistrationDueCalculationService)


class _RegistrationNotificationContext:
    """Minimal stand-in for an ApprovalInstance, same trick used by the
    Maintenance PMS task, so the existing NotificationEngine can fire
    ROLE/SPECIFIC_USER notifications for these non-approval-workflow
    events."""
    def __init__(self, document_type_code, reference_table, reference_id):
        self.document_type = type("DT", (), {"code": document_type_code})()
        self.reference_table = reference_table
        self.reference_id = reference_id
        self.submitted_by = None
        self.current_level = 0
        self.approval_path = None


def auto_generate_due_registrations() -> int:
    """Create DRAFT RENEWAL registrations for DUE_SOON/OVERDUE vehicles
    whose matched RegistrationTemplate policy is AUTO_REGISTRATION, and
    that don't already have an open (non-COMPLETED/CANCELLED) renewal in
    progress. Returns the number of registrations created."""
    from app.modules.system_admin.services.notification_engine import (
        NotificationEngine)
    from datetime import date

    due_service = RegistrationDueCalculationService()
    reg_service = VehicleRegistrationService()
    notif_engine = NotificationEngine()
    created = 0

    for entry in due_service.get_all_due_vehicles():
        vehicle = entry["vehicle"]
        template = entry["template"]
        event_code = ("registration_overdue" if entry["status"] == "OVERDUE"
                      else "registration_due_soon")

        # MANUAL and AUTO_SCHEDULE (recommended default) never
        # auto-create a renewal — only AUTO_REGISTRATION does, same
        # three-tier policy as Maintenance PMS-3.
        if not template or template.next_generation_policy != "AUTO_REGISTRATION":
            context = _RegistrationNotificationContext(
                document_type_code="VR", reference_table="vehicles",
                reference_id=vehicle.id)
            notif_engine.dispatch(event_code, context)
            continue

        existing_open = (VehicleRegistration.query
                         .filter_by(vehicle_id=vehicle.id)
                         .filter(VehicleRegistration.status.notin_(
                             ["COMPLETED", "CANCELLED"]))
                         .first())
        if existing_open:
            continue

        registration = reg_service.create(
            vehicle_id=vehicle.id, registration_type="RENEWAL",
            registration_date=date.today(), user=None)
        created += 1

        context = _RegistrationNotificationContext(
            document_type_code="VR", reference_table="vehicle_registrations",
            reference_id=registration.id)
        notif_engine.dispatch(event_code, context)

    return created


@celery.task(name="vehicle_registration.auto_generate_due_registrations")
def auto_generate_due_registrations_task():
    """Celery task wrapper — actual beat schedule configured at deployment."""
    return auto_generate_due_registrations()
