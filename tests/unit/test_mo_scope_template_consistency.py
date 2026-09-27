"""MaintenanceOrderService.create() -- scope_template_id/maintenance_type_id
consistency.

Reported directly in the FMS AI Developer Prompt spec (item 13,
"Backend Validation"): the create() path fetched scope_template_id by
ID alone and copied its checklist items onto the order with no check
that the template actually belongs to the maintenance_type_id also on
the request. A tampered request -- or simply a client bug -- could pair
a vehicle/maintenance_type with a scope template built for an entirely
different maintenance discipline, and the order would silently get the
wrong checklist. This is the dashboard-originated New Maintenance
Order flow's own "prevent manipulated requests from creating an
inconsistent Maintenance Order" requirement, checked at the one place
that actually matters: server-side, in the service, not trusted from
whatever the frontend happened to send.
"""
import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db
from app.modules.maintenance_config.models import PMScopeTemplate
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, InvalidOrderCategoryError)
from app.modules.user_management.models import Permission, Role, User
from datetime import date


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def env(db):
    sync_permissions()
    branch = BranchService().create(code="BR-CONS", name="Consistency Branch")
    vt = VehicleTypeService().create(code="LV-CONS", name="Light",
                                     category="LIGHT")
    mt_preventive = MaintenanceTypeService().create(
        code="CONS-PREV", name="Preventive", category="PM")
    mt_tires = MaintenanceTypeService().create(
        code="CONS-TIRE", name="Tires", category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Camry", year=2024,
        branch_id=branch.id, conduction_number="CONS-000")
    _ensure_doc_type("MO")
    db.session.commit()
    return {"branch": branch, "vt": vt, "mt_preventive": mt_preventive,
            "mt_tires": mt_tires, "vehicle": vehicle}


class TestScopeTemplateConsistency:
    def test_accepts_a_template_that_matches_the_declared_maintenance_type(
            self, db, env):
        template = PMScopeTemplate(
            name="Preventive package", maintenance_type_id=env["mt_preventive"].id)
        db.session.add(template)
        db.session.commit()

        order = MaintenanceOrderService().create(
            vehicle_id=env["vehicle"].id,
            maintenance_type_id=env["mt_preventive"].id,
            scope_template_id=template.id,
            scheduled_date=date.today(), user=None)
        assert order.scope_template_id == template.id

    def test_rejects_a_template_built_for_a_different_maintenance_type(
            self, db, env):
        # Exactly the tampering scenario the spec describes: a Tires
        # template paired with a Preventive Maintenance order.
        mismatched_template = PMScopeTemplate(
            name="Tires package", maintenance_type_id=env["mt_tires"].id)
        db.session.add(mismatched_template)
        db.session.commit()

        with pytest.raises(InvalidOrderCategoryError):
            MaintenanceOrderService().create(
                vehicle_id=env["vehicle"].id,
                maintenance_type_id=env["mt_preventive"].id,
                scope_template_id=mismatched_template.id,
                scheduled_date=date.today(), user=None)

    def test_a_nonexistent_scope_template_id_is_also_rejected_cleanly(
            self, db, env):
        with pytest.raises(InvalidOrderCategoryError):
            MaintenanceOrderService().create(
                vehicle_id=env["vehicle"].id,
                maintenance_type_id=env["mt_preventive"].id,
                scope_template_id=999999,
                scheduled_date=date.today(), user=None)

    def test_no_scope_template_at_all_is_unaffected(self, db, env):
        # The overwhelming majority of orders (manual, no PM package
        # picked) must be completely unaffected by this check.
        order = MaintenanceOrderService().create(
            vehicle_id=env["vehicle"].id,
            maintenance_type_id=env["mt_preventive"].id,
            scheduled_date=date.today(), user=None)
        assert order.scope_template_id is None
