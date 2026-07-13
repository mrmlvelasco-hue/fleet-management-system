import pytest

from app.extensions import db
from app.modules.master_data.vehicle.service import VehicleService, DuplicateVehicleError
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-RACE", name="Race Branch")
    vt = VehicleTypeService().create(code="LV-RACE", name="Light", category="LIGHT")
    return branch, vt


def test_duplicate_plate_number_raises_friendly_error_even_bypassing_precheck(db, env):
    """Simulates a race condition / any path that inserts past the
    pre-check query (e.g. two near-simultaneous requests) by mocking the
    pre-check to miss the existing duplicate — the actual INSERT still
    hits the real unique constraint, and the service must translate that
    into a friendly DuplicateVehicleError, never a raw IntegrityError."""
    from unittest.mock import patch
    branch, vt = env
    db.session.add(Vehicle(vehicle_type_id=vt.id, brand="Toyota", model="Vios",
                           year=2024, branch_id=branch.id,
                           conduction_number="RACE-000", plate_number="RACE-PLATE"))
    db.session.commit()

    class _FakeQueryResult:
        def first(self):
            return None  # simulates the race window: pre-check finds nothing

    with patch("app.modules.master_data.vehicle.service.Vehicle.query") as mock_query:
        mock_query.filter_by.return_value = _FakeQueryResult()
        with pytest.raises(DuplicateVehicleError) as exc_info:
            VehicleService().create(
                vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
                branch_id=branch.id, conduction_number="RACE-001",
                plate_number="RACE-PLATE")
    assert "already exists" in str(exc_info.value).lower()
    assert "IntegrityError" not in str(exc_info.value)


def test_duplicate_conduction_number_raises_friendly_error_even_bypassing_precheck(db, env):
    branch, vt = env
    db.session.add(Vehicle(vehicle_type_id=vt.id, brand="Toyota", model="Vios",
                           year=2024, branch_id=branch.id,
                           conduction_number="RACE-COND-1"))
    db.session.commit()

    with pytest.raises(DuplicateVehicleError):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
            branch_id=branch.id, conduction_number="RACE-COND-1")


def test_session_recovers_after_duplicate_error_for_further_queries(db, env):
    """After a caught duplicate error, the session must be usable again
    (rolled back cleanly) — not left in a broken transaction state."""
    branch, vt = env
    db.session.add(Vehicle(vehicle_type_id=vt.id, brand="Toyota", model="Vios",
                           year=2024, branch_id=branch.id,
                           conduction_number="RACE-002", plate_number="RACE-PLATE-2"))
    db.session.commit()

    with pytest.raises(DuplicateVehicleError):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
            branch_id=branch.id, conduction_number="RACE-003",
            plate_number="RACE-PLATE-2")

    # Session should still work normally afterward.
    assert Vehicle.query.filter_by(conduction_number="RACE-002").first() is not None
    ok_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Nissan", model="Almera", year=2024,
        branch_id=branch.id, conduction_number="RACE-004")
    assert ok_vehicle.id is not None


def test_multiple_vehicles_with_blank_plate_number_never_conflict(db, env):
    """The common real-world case: many vehicles legitimately have no
    plate number yet (conduction number only) — must never be treated as
    duplicates of each other."""
    branch, vt = env
    v1 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="RACE-005")
    v2 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch.id, conduction_number="RACE-006")
    assert v1.plate_number is None
    assert v2.plate_number is None
