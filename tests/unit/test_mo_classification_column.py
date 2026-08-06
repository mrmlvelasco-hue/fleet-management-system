"""The Maintenance Orders list gained a Classification column reading
the SAME buckets (Preventive / Corrective / Predictive / Operational)
as the Dashboard's "Maintenance Orders by Type" chart -- built from one
shared source of truth so the two can never disagree, and so an
OPERATIONAL order (which has no maintenance_type) no longer shows a
bare, unexplained dash where its type would otherwise be.
"""
from datetime import date

import pytest

from app.modules.transactions.maintenance_order.models import (
    MaintenanceOrder, TransactionType, MAINTENANCE_CLASS_LABELS)


@pytest.fixture()
def fixtures(db):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-MCC", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-MCC", name="Branch MCC")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="MCC-1")
    db.session.commit()
    return vehicle


def _mo(db, vehicle, category, tt_code=None, tt_class=None):
    tt = None
    if tt_code:
        tt = TransactionType(code=tt_code, name=tt_code,
                            order_category=category,
                            maintenance_class=tt_class)
        db.session.add(tt)
        db.session.flush()
    order = MaintenanceOrder(
        vehicle_id=vehicle.id, order_category=category,
        transaction_type_id=tt.id if tt else None,
        scheduled_date=date.today(), status="DRAFT")
    db.session.add(order)
    db.session.commit()
    return order


def test_operational_order_classifies_as_operational_not_blank(
        db, fixtures):
    """The reported gap: an OPERATIONAL order has no maintenance_type
    at all, so it previously showed a bare dash with nothing explaining
    why."""
    order = _mo(db, fixtures, "OPERATIONAL", "OP-MCC", None)
    assert order.maintenance_class_bucket == "OPERATIONAL"
    assert order.maintenance_class_label == "Operational"


def test_preventive_classification_from_transaction_type(db, fixtures):
    order = _mo(db, fixtures, "MAINTENANCE", "PM-MCC", "PREVENTIVE")
    assert order.maintenance_class_bucket == "PREVENTIVE"
    assert order.maintenance_class_label == "Preventive Maintenance"


def test_corrective_classification_from_transaction_type(db, fixtures):
    order = _mo(db, fixtures, "MAINTENANCE", "CM-MCC", "CORRECTIVE")
    assert order.maintenance_class_bucket == "CORRECTIVE"


def test_no_transaction_type_defaults_to_preventive(db, fixtures):
    """Older orders that predate transaction types are preventive by
    definition, matching the dashboard chart's own rule exactly."""
    order = _mo(db, fixtures, "MAINTENANCE", None, None)
    assert order.maintenance_class_bucket == "PREVENTIVE"


def test_list_and_dashboard_chart_agree_on_the_same_data(app, db, fixtures):
    """The actual guarantee that matters: the column shown on the list
    and the count the dashboard chart buckets the same order into must
    never disagree."""
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")

    order = _mo(db, fixtures, "OPERATIONAL", "OP-MCC2", None)

    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"})
    html = client.get("/transactions/maintenance-orders").get_data(as_text=True)
    assert "Operational" in html

    from app.core.dashboard_analytics_service import DashboardAnalyticsService
    chart = DashboardAnalyticsService().maintenance_orders_by_type()
    assert "Operational" in chart["labels"]
    idx = chart["labels"].index("Operational")
    assert chart["data"][idx] >= 1


def test_shared_labels_used_by_both_dashboard_and_list():
    """Confirms there is genuinely one dict, not two independently
    maintained copies that could drift apart."""
    from app.core.dashboard_analytics_service import DashboardAnalyticsService
    import inspect
    source = inspect.getsource(
        DashboardAnalyticsService.maintenance_orders_by_type)
    assert "MAINTENANCE_CLASS_LABELS" in source
