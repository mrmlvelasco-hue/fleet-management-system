"""Two production defects from UAT.

1. Maintenance Orders never routed through the Approval Matrix. submit()
   passed getattr(record, "amount", None) -- but MaintenanceOrder has no
   `amount` column at all (only estimated_cost/actual_cost); only
   PurchaseRequest defines `amount`. So every MO resolved with
   amount=None, which matches ONLY a matrix whose min AND max are both
   NULL. A correctly configured matrix with real bands matched nothing.

2. Migrated vehicles couldn't be renewed. Their registration history is
   imported onto Vehicle.last_known_registration_expiry, not as a
   VehicleRegistration row, so the RENEWAL guard saw no prior
   registration and blocked it.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.modules.transactions.base_service import BaseTransactionService


class _FakeMO:
    """Mirrors MaintenanceOrder: no `amount`, has estimated_cost."""
    estimated_cost = Decimal("15000")
    actual_cost = None


class _FakePR:
    """Mirrors PurchaseRequest: has `amount`."""
    amount = Decimal("75000")
    estimated_cost = None


def _svc():
    return BaseTransactionService.__new__(BaseTransactionService)


def test_maintenance_order_routes_on_estimated_cost(db):
    """The core regression: an MO must not resolve its matrix with None."""
    assert _svc()._approval_amount(_FakeMO()) == Decimal("15000")


def test_purchase_request_still_routes_on_amount(db):
    """The fallback must not change modules that already worked."""
    assert _svc()._approval_amount(_FakePR()) == Decimal("75000")


def test_record_with_no_monetary_field_yields_none(db):
    class _NoMoney:
        pass
    assert _svc()._approval_amount(_NoMoney()) is None


def test_zero_cost_is_not_treated_as_missing(db):
    """0 is a real amount and must route to a 0-N band, not fall through
    to the next field or to None."""
    class _Zero:
        amount = None
        estimated_cost = Decimal("0")
    assert _svc()._approval_amount(_Zero()) == Decimal("0")


def test_matrix_resolves_the_correct_band(db):
    from app.modules.approval_config.service import ApprovalMatrixService
    from app.modules.approval_config.models import ApprovalMatrix, ApprovalPath

    low = ApprovalPath(name="Low Value Path")
    high = ApprovalPath(name="High Value Path")
    db.session.add_all([low, high])
    db.session.flush()
    db.session.add(ApprovalMatrix(document_type_id=4, min_amount=0,
                                  max_amount=49000,
                                  approval_path_id=low.id))
    db.session.add(ApprovalMatrix(document_type_id=4, min_amount=50000,
                                  max_amount=20000000,
                                  approval_path_id=high.id))
    db.session.commit()

    svc = ApprovalMatrixService()
    assert svc.resolve(4, Decimal("15000")).approval_path_id == low.id
    assert svc.resolve(4, Decimal("75000")).approval_path_id == high.id
    # Boundaries are inclusive on both ends.
    assert svc.resolve(4, Decimal("49000")).approval_path_id == low.id
    assert svc.resolve(4, Decimal("50000")).approval_path_id == high.id


def test_no_matrix_error_names_the_amount_and_date(db):
    """The old message named neither, so diagnosing a routing failure
    meant reading source."""
    from app.modules.approval_config.service import (
        ApprovalMatrixService, NoMatrixError)
    with pytest.raises(NoMatrixError) as exc:
        ApprovalMatrixService().resolve(999, Decimal("15000"))
    message = str(exc.value)
    assert "15,000" in message
    assert "Approval Matrix" in message


# ── Registration renewal for migrated vehicles ──────────────────────────────

@pytest.fixture()
def vehicles(db):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-RN", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-RN", name="Branch")
    migrated = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2015,
        branch_id=branch.id, conduction_number="RN-MIG")
    migrated.last_known_registration_expiry = date(2026, 1, 31)
    never = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="RN-NEW")
    db.session.commit()
    return migrated, never


def test_migrated_vehicle_can_be_renewed(db, vehicles):
    """Its history is real -- it just lives on the vehicle rather than as
    a registration row."""
    from app.modules.transactions.vehicle_registration.service import (
        VehicleRegistrationService)
    migrated, _ = vehicles
    reg = VehicleRegistrationService().create(
        vehicle_id=migrated.id, registration_type="RENEWAL",
        registration_date=date.today(), user=None)
    assert reg.id is not None


def test_vehicle_never_registered_is_still_blocked(db, vehicles):
    """The guard must still catch a genuine mistake, not be removed."""
    from app.modules.transactions.vehicle_registration.service import (
        VehicleRegistrationService, NoExistingRegistrationError)
    _, never = vehicles
    with pytest.raises(NoExistingRegistrationError):
        VehicleRegistrationService().create(
            vehicle_id=never.id, registration_type="RENEWAL",
            registration_date=date.today(), user=None)


def test_block_message_explains_the_migration_case(db, vehicles):
    from app.modules.transactions.vehicle_registration.service import (
        VehicleRegistrationService, NoExistingRegistrationError)
    _, never = vehicles
    with pytest.raises(NoExistingRegistrationError) as exc:
        VehicleRegistrationService().create(
            vehicle_id=never.id, registration_type="RENEWAL",
            registration_date=date.today(), user=None)
    assert "migrated" in str(exc.value).lower()
