"""Vehicle Handover Checklist -- issuance/return, standalone.

Deliberately independent of the periodic-inspection checklist module
(ChecklistTemplate / ChecklistDocument) and of the mobile APK.

Michael: "This separate to the checklist module and APK because if my
clients get this system excluded the checklist and APK this will be the
alternative ways for checklist." So nothing here may import from, join
against, or depend on the existence of a single row in
checklist_templates, checklist_categories, checklist_items, or
checklist_documents. If those tables were dropped entirely this module
must keep working, because that is precisely the scenario it exists to
cover.

It reuses only INFRASTRUCTURE that is not itself the checklist module:
the generic Auto Numbering Engine, the generic Attachment service, and
BaseModel's audit columns -- the same things Vehicle Registration or a
Purchase Request reuse, and none of them checklist-specific.
"""
import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.transactions.vehicle_handover.models import (
    HandoverDamageMark, HandoverItem, VehicleHandoverChecklist)
from app.modules.transactions.vehicle_handover.service import (
    VehicleHandoverService)
from app.modules.user_management.models import Role, User


def test_module_has_no_import_of_the_checklist_module():
    """The independence guarantee, checked structurally.

    Not just "we didn't call anything" -- scanned for actual IMPORT
    statements, so a future edit that reaches for ChecklistItem cannot
    land unnoticed. Deliberately not a plain substring search across
    the whole file: both modules' docstrings legitimately mention the
    other by name to explain the boundary, and a substring check would
    flag that prose as a violation it is not.
    """
    import ast
    import inspect
    from app.modules.transactions.vehicle_handover import models, service

    for module in (models, service):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "vehicle_checklist" not in node.module, (
                    f"{module.__name__} imports from {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "vehicle_checklist" not in alias.name, (
                        f"{module.__name__} imports {alias.name}")


@pytest.fixture()
def rig(db, app):
    sync_permissions()
    db.session.commit()
    branch = Branch(name="Head Office", code="HO")
    db.session.add(branch)
    db.session.flush()
    vtype = VehicleType(code="CARL", name="Car Light", category="CAR")
    db.session.add(vtype)
    db.session.flush()
    vehicle = Vehicle(plate_number="NAG 1234", brand="Toyota", model="Hilux",
                      variant="2022 G", year=2022, vehicle_type_id=vtype.id,
                      branch_id=branch.id, is_active=True, status="ACTIVE",
                      current_odometer=125432)
    db.session.add(vehicle)
    user = User(username="mreyes", email="m@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [Role(name="Fleet Officer")]
    db.session.add(user)
    db.session.flush()
    driver = Driver(first_name="Juan", last_name="dela Cruz",
                    employee_number="EMP-0099", branch_id=branch.id,
                    is_active=True)
    db.session.add(driver)
    db.session.commit()
    return {"vehicle": vehicle, "user": user, "driver": driver, "branch": branch}


class TestCreateAndNumber:
    def test_create_assigns_a_document_number(self, rig, db, app):
        svc = VehicleHandoverService()
        doc = svc.create(vehicle_id=rig["vehicle"].id,
                         assignee_driver_id=rig["driver"].id,
                         actor=rig["user"])
        assert doc.document_number
        assert doc.document_number.startswith("VHC-")

    def test_document_numbers_do_not_collide(self, rig, db):
        svc = VehicleHandoverService()
        a = svc.create(vehicle_id=rig["vehicle"].id, actor=rig["user"])
        b = svc.create(vehicle_id=rig["vehicle"].id, actor=rig["user"])
        assert a.document_number != b.document_number

    def test_new_record_is_draft(self, rig):
        doc = VehicleHandoverService().create(
            vehicle_id=rig["vehicle"].id, actor=rig["user"])
        assert doc.status == "DRAFT"

    def test_assignee_is_snapshotted_not_just_linked(self, rig, db):
        """The name is copied onto the document at creation time.

        A later edit to the Driver record (a spelling fix, a promotion)
        must not silently rewrite a document someone already signed --
        the same principle the PM template audit relied on for
        vehicle_make/vehicle_brand.
        """
        doc = VehicleHandoverService().create(
            vehicle_id=rig["vehicle"].id, assignee_driver_id=rig["driver"].id,
            actor=rig["user"])
        assert doc.assignee_name == "Juan dela Cruz"
        rig["driver"].first_name = "Juanito"
        db.session.commit()
        db.session.refresh(doc)
        assert doc.assignee_name == "Juan dela Cruz"


class TestLifecycle:
    def test_issue_stamps_date_and_advances_status(self, rig):
        svc = VehicleHandoverService()
        doc = svc.create(vehicle_id=rig["vehicle"].id, actor=rig["user"])
        svc.issue(doc.id, odometer=125432, fuel_level=4,
                 issued_by_name="M. Reyes", received_by_name="J. Dela Cruz",
                 actor=rig["user"])
        assert doc.status == "ISSUED"
        assert doc.date_issued is not None
        assert doc.odometer_at_issuance == 125432

    def test_cannot_issue_twice(self, rig):
        svc = VehicleHandoverService()
        doc = svc.create(vehicle_id=rig["vehicle"].id, actor=rig["user"])
        svc.issue(doc.id, actor=rig["user"])
        with pytest.raises(ValueError):
            svc.issue(doc.id, actor=rig["user"])

    def test_return_requires_issuance_first(self, rig):
        """The paper form's own ordering: you cannot fill in the Return
        column of a unit that was never issued."""
        doc = VehicleHandoverService().create(
            vehicle_id=rig["vehicle"].id, actor=rig["user"])
        with pytest.raises(ValueError):
            VehicleHandoverService().return_unit(doc.id, actor=rig["user"])

    def test_return_closes_the_record(self, rig):
        svc = VehicleHandoverService()
        doc = svc.create(vehicle_id=rig["vehicle"].id, actor=rig["user"])
        svc.issue(doc.id, actor=rig["user"])
        svc.return_unit(doc.id, odometer=125600,
                        returned_by_name="J. Dela Cruz",
                        received_by_name="M. Reyes", actor=rig["user"])
        assert doc.status == "RETURNED"
        assert doc.date_returned is not None
        assert doc.odometer_at_return == 125600


class TestItemsAndDamage:
    def test_default_item_set_is_seeded_on_create(self, rig, db):
        """Matches the paper form's ~40-line accessory list.

        Not read from ChecklistItem -- see the module-independence test
        above. This is this module's OWN small, hardcoded default,
        because the paper form's item list is the specification here,
        not a configurable catalogue.
        """
        doc = VehicleHandoverService().create(
            vehicle_id=rig["vehicle"].id, actor=rig["user"])
        names = [i.item_name for i in doc.items]
        assert "Keys" in names
        assert "Spare Tire" in names
        assert "Tool Bag" in names
        assert len(names) >= 30

    def test_marking_an_item_records_qty_separately_from_condition(self, rig, db):
        doc = VehicleHandoverService().create(
            vehicle_id=rig["vehicle"].id, actor=rig["user"])
        item = next(i for i in doc.items if i.item_name == "Ashtray")
        VehicleHandoverService().mark_item(
            item.id, phase="return", mark="OK", qty=2, actor=rig["user"])
        db.session.refresh(item)
        assert item.return_mark == "OK"
        assert item.return_qty == 2
        assert item.issuance_mark is None

    def test_add_damage_mark_stores_percentage_position(self, rig, db):
        doc = VehicleHandoverService().create(
            vehicle_id=rig["vehicle"].id, actor=rig["user"])
        mark = VehicleHandoverService().add_damage(
            doc.id, kind="SCRATCH", x=25.5, y=60.0,
            note="front bumper", actor=rig["user"])
        assert mark.x == 25.5 and mark.y == 60.0
        assert mark in doc.damages

    def test_rejects_an_unknown_damage_kind(self, rig):
        doc = VehicleHandoverService().create(
            vehicle_id=rig["vehicle"].id, actor=rig["user"])
        with pytest.raises(ValueError):
            VehicleHandoverService().add_damage(
                doc.id, kind="EXPLOSION", x=1, y=1, actor=rig["user"])


class TestReportPayload:
    def test_to_report_shape_matches_the_react_print_component(self, rig):
        """The service's output must be a drop-in prop for
        VehicleChecklistReport (react-v191) with no reshaping in the
        route handler -- that reshaping is exactly where a field gets
        silently dropped.
        """
        svc = VehicleHandoverService()
        doc = svc.create(vehicle_id=rig["vehicle"].id,
                         assignee_driver_id=rig["driver"].id,
                         actor=rig["user"])
        svc.issue(doc.id, odometer=125432, fuel_level=4, actor=rig["user"])
        payload = svc.to_report(doc.id)
        assert payload["assignee_name"] == "Juan dela Cruz"
        assert payload["identification"][0]["label"] == "Plate No. / CS No."
        assert len(payload["columns"]) == 3
        assert isinstance(payload["documents"], list)
        assert isinstance(payload["signatures"], list)


class TestVisibility:
    def test_restricted_assignee_sees_only_their_own_handover(self, rig, db):
        """The same restrict_to_assigned_vehicles rule (flask-v251)
        applies here, because an issuance/return record is exactly the
        kind of document a restricted assignee should be able to see
        for their own vehicle and no one else's."""
        from app.modules.master_data.vehicle.assignment_models import (
            VehicleAssignment)
        other_vehicle = Vehicle(plate_number="OTH-999", brand="Toyota",
                               model="Vios", year=2020,
                               vehicle_type_id=rig["vehicle"].vehicle_type_id,
                               branch_id=rig["branch"].id, is_active=True,
                               status="ACTIVE")
        db.session.add(other_vehicle)
        db.session.flush()

        assignee_user = User(username="assignee", email="a@e.com",
                             password_hash=hash_password("secret123"),
                             is_active=True,
                             restrict_to_assigned_vehicles=True)
        db.session.add(assignee_user)
        db.session.flush()
        driver = Driver(first_name="A", last_name="Ssignee",
                        employee_number="EMP-0100", user_id=assignee_user.id,
                        branch_id=rig["branch"].id, is_active=True)
        db.session.add(driver)
        db.session.flush()
        db.session.add(VehicleAssignment(vehicle_id=rig["vehicle"].id,
                                         driver_id=driver.id,
                                         assigned_to=None, source="MANUAL"))
        db.session.commit()

        svc = VehicleHandoverService()
        mine = svc.create(vehicle_id=rig["vehicle"].id, actor=rig["user"])
        others = svc.create(vehicle_id=other_vehicle.id, actor=rig["user"])

        visible = svc.list_visible(assignee_user)
        ids = [d.id for d in visible]
        assert mine.id in ids
        assert others.id not in ids
