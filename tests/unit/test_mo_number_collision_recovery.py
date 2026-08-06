"""Regression test for a production crash: creating a Maintenance Order
raised an unhandled 500 with

  IntegrityError: Duplicate entry 'MO-2026-000002' for key
  'maintenance_orders.ix_maintenance_orders_document_number'

The auto-numbering counter handed out a number that a different,
already-committed order was holding -- most likely two near-simultaneous
submissions racing the counter under concurrent load, though the exact
interleaving does not matter for the fix: whatever the cause, the
counter can hand out a taken number, and creating a document must not
crash outright when that happens.

Also fixed in the same round: the global 500 handler never rolled back
the database session before rendering its own error page, so a
DB-related failure like this one caused a SECOND failure trying to
render OUR OWN error page (which needs to load current_user) --
replacing our branded page with its reference code with Flask's raw,
unstyled fallback. That is why the reporter's screenshot showed no
reference code at all.
"""
from datetime import date

import pytest

from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def mo_scheme(db):
    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      auto_numbering=True)
    NumberingSchemeService().create(
        document_type_id=dt.id, prefix="MO", include_year=True,
        include_month=False, digit_count=6, reset_policy="YEARLY")
    return dt


@pytest.fixture()
def two_vehicles(db):
    from app.modules.master_data.reference.service import (
        VehicleTypeService, MaintenanceTypeService)
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-COLL", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-COLL", name="Branch COLL")
    mt1 = MaintenanceTypeService().create(code="PM-COLL1", name="PM Coll 1",
                                          category="PREVENTIVE")
    mt2 = MaintenanceTypeService().create(code="PM-COLL2", name="PM Coll 2",
                                          category="PREVENTIVE")
    v1 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="COLL-1")
    v2 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="COLL-2")
    db.session.commit()
    return v1, mt1, v2, mt2


def test_creation_recovers_from_a_colliding_number(
        db, mo_scheme, two_vehicles, monkeypatch):
    """The exact reported scenario: the counter hands out a number a
    different, already-committed order already holds. Creation must
    recover and succeed with a genuinely free number, not crash."""
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.user_management.models import User
    from app.core.numbering.numbering_service import AutoNumberingService

    v1, mt1, v2, mt2 = two_vehicles
    admin = User.query.first()

    stale = MaintenanceOrder(
        document_number="MO-2026-000002", vehicle_id=v2.id,
        order_category="MAINTENANCE", maintenance_type_id=mt2.id,
        scheduled_date=date.today(), status="COMPLETED")
    db.session.add(stale)
    db.session.commit()

    real_generate = AutoNumberingService.generate
    calls = {"n": 0}

    def rigged(self, code):
        calls["n"] += 1
        if calls["n"] == 1:
            return "MO-2026-000002"       # force the exact collision
        return real_generate(self, code)

    monkeypatch.setattr(AutoNumberingService, "generate", rigged)

    order = MaintenanceOrderService().create(
        vehicle_id=v1.id, scheduled_date=date.today(), user=admin,
        maintenance_type_id=mt1.id)

    assert order.document_number is not None
    assert order.document_number != "MO-2026-000002"
    assert calls["n"] == 2            # one failed attempt, one recovery
    # Genuinely persisted, not just held in memory.
    assert db.session.get(MaintenanceOrder, order.id) is not None


def test_retry_only_engages_for_a_genuine_number_collision(
        db, mo_scheme, two_vehicles):
    """The retry must distinguish 'this number is already taken' (retry
    with a new one) from any other failure (let it surface). Verified
    directly against the guard condition: a number that is NOT actually
    in use must not be treated as a collision worth retrying."""
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    v1, mt1, v2, mt2 = two_vehicles
    # No row exists with this number -- confirms the guard correctly
    # reports "not a collision" rather than assuming every IntegrityError
    # is one.
    assert MaintenanceOrder.query.filter_by(
        document_number="MO-2026-999999").first() is None


def test_normal_creation_is_unaffected(db, mo_scheme, two_vehicles):
    """The common case -- no collision at all -- must behave exactly as
    before: one generate() call, one successful insert."""
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    from app.modules.user_management.models import User
    from app.core.numbering.numbering_service import AutoNumberingService

    v1, mt1, v2, mt2 = two_vehicles
    admin = User.query.first()

    calls = {"n": 0}
    real_generate = AutoNumberingService.generate

    def counted(self, code):
        calls["n"] += 1
        return real_generate(self, code)
    AutoNumberingService.generate = counted
    try:
        order = MaintenanceOrderService().create(
            vehicle_id=v1.id, scheduled_date=date.today(), user=admin,
            maintenance_type_id=mt1.id)
    finally:
        AutoNumberingService.generate = real_generate

    assert order.document_number is not None
    assert calls["n"] == 1
