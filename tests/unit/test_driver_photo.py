"""Driver/assignee photo requirement.

Reported: the printed Vehicle Assignment Memo has an empty photo box --
no driver record has anywhere to store a photo. Requested that every
new driver or assignee record require one, and that it actually print.
"""
import io

import pytest


def _tiny_jpeg():
    """Minimal valid JPEG bytes, small enough not to need Pillow."""
    return io.BytesIO(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605"
        "08070707090908090a141009090a1e14141117141a1a1a1a1a1a1a1a1a1a1a"
        "1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a"
        "1a1a1a1a1a1affc9000b080001000101011100ffcc00060010100501ffda00"
        "080101000000013fd2ffd9"))


@pytest.fixture()
def branch(db):
    from app.modules.master_data.org.service import BranchService
    return BranchService().create(code="BR-PHOTOTEST", name="Photo Test")


def test_creating_a_driver_without_a_photo_is_rejected(db, branch):
    from app.modules.master_data.driver.service import (
        DriverService, InvalidAssigneeError)
    with pytest.raises(InvalidAssigneeError):
        DriverService().create(
            employee_number="EMP-NOPHOTO-1", first_name="No",
            last_name="Photo", branch_id=branch.id,
            assignee_type="EMPLOYEE")


def test_rejected_creation_leaves_no_partial_driver_record(db, branch):
    """The validation must happen before anything is written -- no
    half-created driver row left behind by a rejected photo-less
    attempt."""
    from app.modules.master_data.driver.service import (
        DriverService, InvalidAssigneeError)
    from app.modules.master_data.driver.models import Driver
    try:
        DriverService().create(
            employee_number="EMP-NOPHOTO-2", first_name="No",
            last_name="Photo", branch_id=branch.id,
            assignee_type="EMPLOYEE")
    except InvalidAssigneeError:
        pass
    assert Driver.query.filter_by(
        employee_number="EMP-NOPHOTO-2").first() is None


def test_creating_a_driver_with_a_photo_succeeds(app, db, branch):
    from werkzeug.datastructures import FileStorage
    from app.modules.master_data.driver.service import DriverService

    photo = FileStorage(stream=_tiny_jpeg(), filename="photo.jpg",
                        content_type="image/jpeg")
    with app.test_request_context():
        driver = DriverService().create(
            employee_number="EMP-HASPHOTO-1", first_name="Has",
            last_name="Photo", branch_id=branch.id,
            assignee_type="EMPLOYEE", photo_file=photo)
    assert driver.photo_attachment_id is not None


def test_editing_an_existing_driver_does_not_require_a_new_photo(
        app, db, branch):
    """A legacy driver record with no photo must still be editable for
    an unrelated change -- the requirement is for NEW records only."""
    from app.modules.master_data.driver.models import Driver
    from app.modules.master_data.driver.service import DriverService

    legacy = Driver(person_id="PID-EDIT-1", employee_number="EMP-EDIT-1",
                    first_name="Legacy", last_name="One",
                    assignee_type="EMPLOYEE", branch_id=branch.id)
    db.session.add(legacy)
    db.session.commit()

    updated = DriverService().update(legacy.id, first_name="Updated")
    assert updated.first_name == "Updated"
    assert updated.photo_attachment_id is None   # still not required


def test_photo_can_be_added_to_an_existing_driver(app, db, branch):
    from werkzeug.datastructures import FileStorage
    from app.modules.master_data.driver.models import Driver
    from app.modules.master_data.driver.service import DriverService

    legacy = Driver(person_id="PID-EDIT-2", employee_number="EMP-EDIT-2",
                    first_name="Legacy", last_name="Two",
                    assignee_type="EMPLOYEE", branch_id=branch.id)
    db.session.add(legacy)
    db.session.commit()

    photo = FileStorage(stream=_tiny_jpeg(), filename="added.jpg",
                        content_type="image/jpeg")
    with app.test_request_context():
        updated = DriverService().update(legacy.id, photo_file=photo)
    assert updated.photo_attachment_id is not None


def test_vam_print_shows_the_photo_when_one_exists(app, db, branch):
    from datetime import date
    from werkzeug.datastructures import FileStorage
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    from app.modules.master_data.driver.service import DriverService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder, TransactionType)

    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")

    photo = FileStorage(stream=_tiny_jpeg(), filename="vam.jpg",
                        content_type="image/jpeg")
    with app.test_request_context():
        driver = DriverService().create(
            employee_number="EMP-VAM-1", first_name="Vam",
            last_name="Test", branch_id=branch.id,
            assignee_type="EMPLOYEE", photo_file=photo)

    vt = VehicleTypeService().create(code="LV-VAM", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, conduction_number="VAM-1")
    order = MaintenanceOrder(
        vehicle_id=vehicle.id, order_category="OPERATIONAL",
        driver_id=driver.id, assignment_classification="TOOL_OF_THE_TRADE",
        scheduled_date=date.today(), status="DRAFT")
    db.session.add(order)
    db.session.commit()

    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"})
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam"
    ).get_data(as_text=True)
    assert "No Photo on File" not in html
    assert f"/attachments/{driver.photo_attachment_id}/view" in html


def test_vam_print_shows_fallback_text_for_a_driver_with_no_photo(
        app, db, branch):
    from datetime import date
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    from app.modules.master_data.driver.models import Driver
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")

    legacy = Driver(person_id="PID-VAM-2", employee_number="EMP-VAM-2",
                    first_name="NoPhoto", last_name="Test",
                    assignee_type="EMPLOYEE", branch_id=branch.id)
    db.session.add(legacy)
    db.session.commit()

    vt = VehicleTypeService().create(code="LV-VAM2", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, conduction_number="VAM-2")
    order = MaintenanceOrder(
        vehicle_id=vehicle.id, order_category="OPERATIONAL",
        driver_id=legacy.id, assignment_classification="TOOL_OF_THE_TRADE",
        scheduled_date=date.today(), status="DRAFT")
    db.session.add(order)
    db.session.commit()

    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"})
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}/print-vam"
    ).get_data(as_text=True)
    assert "No Photo on File" in html
