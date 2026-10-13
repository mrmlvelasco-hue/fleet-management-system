"""Scheduled (Celery beat, daily) expiry of lapsed Authority To Drive
permits -- the physical-lifecycle counterpart of the PM and registration
due scans in maintenance_order/tasks.py and
vehicle_registration/tasks.py, and the same shape.

Nothing used to move an ATD out of ACTIVE (KI-10), so a permit from last
month still read "APPROVED & ACTIVE" in every list. The edit policy made
that worse than cosmetic: an ongoing document is editable, so an
abandoned permit stayed editable for ever. ATDService.editable_scope
closes a lapsed permit by date regardless of whether this has run, so
this scan is for the LABEL, not the rule -- it can be late, or never
configured, without opening anything up.
"""
from app.core.celery_app import celery
from app.modules.transactions.atd.service import ATDService


def expire_overdue_atds() -> int:
    """Stamp every ACTIVE permit whose Valid To has passed as EXPIRED.
    Returns the number changed. Safe to run repeatedly."""
    return ATDService().expire_overdue()


@celery.task(name="atd.expire_overdue")
def expire_overdue_atds_task():
    """Celery task wrapper — actual beat schedule configured at deployment."""
    return expire_overdue_atds()
