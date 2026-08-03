"""Vehicle status could not be changed from the edit form.

Reported: selecting INACTIVE and saving appeared to work but the vehicle
stayed ACTIVE. The form has always POSTed `status`, but _vehicle_fields()
-- which builds the kwargs passed to VehicleService.update() -- never
read it, so the value was silently discarded before it reached the
service. No error, no warning, no change.
"""
import pytest

from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vehicle.models import Vehicle


@pytest.fixture()
def vehicle(db):
    vt = VehicleTypeService().create(code="LV-ST", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-ST", name="Branch")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="ST-1",
        plate_number="STA-111")
    db.session.commit()
    return v


@pytest.mark.parametrize("new_status",
                         ["INACTIVE", "IN_REPAIR", "DISPOSED", "ACTIVE"])
def test_status_can_be_changed_to_each_option(db, vehicle, new_status):
    VehicleService().update(vehicle.id, status=new_status)
    db.session.expire_all()
    assert db.session.get(Vehicle, vehicle.id).status == new_status


def test_vehicle_fields_collects_status_from_the_form(app, db, vehicle):
    """The regression itself: the helper must put `status` in the dict it
    hands to update(). Asserted directly, because the service layer was
    never the problem -- the value never got that far."""
    from app.modules.master_data.routes import _vehicle_fields
    with app.test_request_context(method="POST", data={
            "vehicle_type_id": vehicle.vehicle_type_id,
            "brand": "Toyota", "model": "Hilux", "year": "2022",
            "branch_id": vehicle.branch_id, "current_odometer": "100",
            "status": "INACTIVE"}):
        fields = _vehicle_fields(include_conduction=False)
    assert fields.get("status") == "INACTIVE"


def test_absent_status_is_omitted_rather_than_blanked(app, db, vehicle):
    """A form that doesn't render the field must not wipe the vehicle's
    status as a side effect -- omitting the key is correct, writing ""
    would be worse than the original bug."""
    from app.modules.master_data.routes import _vehicle_fields
    with app.test_request_context(method="POST", data={
            "vehicle_type_id": vehicle.vehicle_type_id,
            "brand": "Toyota", "model": "Hilux", "year": "2022",
            "branch_id": vehicle.branch_id, "current_odometer": "100"}):
        fields = _vehicle_fields(include_conduction=False)
    assert "status" not in fields
