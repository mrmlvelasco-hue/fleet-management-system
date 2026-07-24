"""Tests for the "For My Action" worklist showing "(no number)" for a
document that really does have a number.

Root cause: ApprovalTask.document_number is a DENORMALISED copy taken
when the task is created. If the source record had no number at that
moment, the copy stays null forever -- even once the document itself has
one -- so an approver's queue showed "(no number)" while the Maintenance
Orders list showed the real MO number.

Two defences, both tested here:
  * submit() now makes sure the record HAS its number before the task
    takes its copy (stops it happening again);
  * the worklist falls back to the LIVE record when the copy is missing
    (self-heals every task already sitting in the queue, no migration).
"""
from datetime import date

import pytest

from app.cli import _seed_transaction_types
from app.core.reference_resolver import get_document_number
from app.core.security.registry import sync_permissions
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)


@pytest.fixture()
def order(db):
    sync_permissions()
    _seed_transaction_types()
    # The approval engine refuses an unknown document type, so the MO
    # type must exist for the submit-path tests below.
    from app.modules.document_config.models import DocumentType
    if not DocumentType.query.filter_by(code="MO").first():
        db.session.add(DocumentType(code="MO", name="Maintenance Order",
                                    requires_approval=False))
    db.session.commit()
    vt = VehicleTypeService().create(code="LV-NUM", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="BATT-NUM",
                                         name="Battery Replacement",
                                         category="PREVENTIVE")
    branch = BranchService().create(code="BR-NUM", name="Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="ELF NKR", year=2015,
        branch_id=branch.id, conduction_number="NUM-0",
        plate_number="AVA-4017")
    return MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)


def test_live_lookup_returns_the_real_number_when_the_copy_is_stale(
        db, order):
    """The core of the fix: even if a task's stored copy is null, the
    real number is recoverable from the record itself."""
    order.document_number = "MO-2026-000020"
    db.session.commit()

    resolved = get_document_number("maintenance_orders", order.id)
    assert resolved == "MO-2026-000020"


def test_live_lookup_falls_back_gracefully_for_a_missing_record(db):
    """An orphaned task (its document deleted) must not crash the
    dashboard -- it degrades to a readable placeholder."""
    resolved = get_document_number("maintenance_orders", 999999)
    assert resolved.startswith("maintenance_orders")


def test_live_lookup_handles_a_record_with_genuinely_no_number(db, order):
    order.document_number = None
    db.session.commit()
    resolved = get_document_number("maintenance_orders", order.id)
    # Falls back rather than returning None, so the caller can detect it.
    assert resolved.startswith("maintenance_orders")


def test_submit_backfills_a_missing_document_number(db, order, monkeypatch):
    """Prevention half: a record must not reach the approval queue
    without a number, or its task inherits the null forever.

    The backfill runs BEFORE the approval engine is invoked, so this
    asserts on the record itself and tolerates the engine then failing
    for want of a full approval matrix (which this fixture deliberately
    doesn't build -- that's covered by the approval tests)."""
    order.document_number = None
    db.session.commit()

    monkeypatch.setattr(
        "app.core.numbering.numbering_service.AutoNumberingService.generate",
        lambda self, code: "MO-2026-000099")

    try:
        MaintenanceOrderService().submit(order.id, user=None)
    except Exception:
        pass  # engine config is out of scope for this test
    assert order.document_number == "MO-2026-000099"


def test_submit_still_works_when_numbering_is_unavailable(db, order,
                                                          monkeypatch):
    """A numbering failure must never block submission -- it is caught
    and the worklist's live fallback covers the display side."""
    order.document_number = None
    db.session.commit()

    def _boom(self, code):
        raise RuntimeError("no numbering scheme configured")

    monkeypatch.setattr(
        "app.core.numbering.numbering_service.AutoNumberingService.generate",
        _boom)

    # The numbering error must be swallowed, not propagated.
    try:
        MaintenanceOrderService().submit(order.id, user=None)
    except RuntimeError as exc:
        if "numbering scheme" in str(exc):
            raise AssertionError(
                "numbering failure must not propagate out of submit()")
    except Exception:
        pass  # unrelated approval-config error is fine here
    assert order.document_number is None  # unchanged, but no crash


def test_submit_does_not_overwrite_an_existing_number(db, order,
                                                      monkeypatch):
    order.document_number = "MO-2026-000020"
    db.session.commit()

    monkeypatch.setattr(
        "app.core.numbering.numbering_service.AutoNumberingService.generate",
        lambda self, code: "MO-SHOULD-NOT-BE-USED")

    try:
        MaintenanceOrderService().submit(order.id, user=None)
    except Exception:
        pass
    assert order.document_number == "MO-2026-000020"
