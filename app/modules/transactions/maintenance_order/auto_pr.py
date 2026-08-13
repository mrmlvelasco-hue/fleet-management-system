"""Auto-generation of a draft Purchase Request when a Maintenance Order
reaches final approval.

Wired to the Approval Engine's "approved_final" event rather than to a
particular route, so it fires however the order got approved -- through
the UI, the API, or a document type configured to need no approval at
all (the engine still emits the event in that case).
"""
import logging

logger = logging.getLogger(__name__)


def register_auto_pr(app):
    from app.core.approval.engine import ApprovalEngine

    def _on_approval_event(name, instance):
        if name != "approved_final":
            return
        if instance.reference_table != "maintenance_orders":
            return
        _generate_for_order(instance.reference_id)

    ApprovalEngine().on_event(_on_approval_event)


def _generate_for_order(order_id):
    from app.extensions import db
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    order = db.session.get(MaintenanceOrder, order_id)
    if order is None:
        return None
    if not _auto_pr_enabled(order):
        return None
    try:
        # Requested by whoever raised the order -- they are the person
        # who stated the requirement, so the PR is attributed to them
        # rather than to whichever approver happened to click last.
        requester = order.requester if hasattr(order, "requester") else None
        return MaintenanceOrderService().generate_purchase_request(
            order.id, user=requester)
    except Exception:
        # A failure here must never roll back or block an approval that
        # has already been legitimately granted. The order stays
        # approved; the PR can be raised manually and the cause is in
        # the log.
        logger.exception(
            "Auto Purchase Request generation failed for maintenance "
            "order %s; the approval itself is unaffected.", order_id)
        db.session.rollback()
        return None


def _auto_pr_enabled(order) -> bool:
    """Configurable per the enhancement spec, defaulting to ON.

    Read from System Parameters so it can be turned off without a code
    change. PMS and corrective/operational orders are separately
    switchable because a client may reasonably want one automated and
    not the other.
    """
    from app.modules.system_admin.services.system_parameter_service import (
        SystemParameterService)
    key = ("AUTO_PR_FROM_PMS"
          if (order.category or "").upper() == "PREVENTIVE"
          else "AUTO_PR_FROM_MO")
    try:
        raw = SystemParameterService().get(key, default="YES")
    except Exception:
        raw = "YES"
    return str(raw).strip().upper() in ("YES", "TRUE", "1", "ON")
