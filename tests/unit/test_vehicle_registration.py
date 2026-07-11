from datetime import date

import pytest

from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService, DuplicateActiveRegistrationError,
    NoExistingRegistrationError)
from app.modules.transactions.vehicle_registration.models import (
    VehicleRegistration)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-VR", name="VR Branch")
    vt = VehicleTypeService().create(code="LV-VR", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2026,
        branch_id=branch.id, conduction_number="VR-000")
    user = User(username="vr_requester", email="vr@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()

    dt = DocumentTypeService().create(code="VR", name="Vehicle Registration",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="VR",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return vehicle, user


def test_create_new_registration_defaults_3_year_validity(db, env):
    vehicle, user = env
    svc = VehicleRegistrationService()
    reg = svc.create(vehicle_id=vehicle.id, registration_type="NEW",
                     registration_date=date(2026, 1, 1), user=user)
    assert reg.document_number.startswith("VR-")
    assert reg.validity_years == 3
    assert reg.expiry_date == date(2029, 1, 1)


def test_cannot_create_second_new_registration_while_active(db, env):
    vehicle, user = env
    svc = VehicleRegistrationService()
    svc.create(vehicle_id=vehicle.id, registration_type="NEW",
              registration_date=date(2026, 1, 1), user=user)
    with pytest.raises(DuplicateActiveRegistrationError):
        svc.create(vehicle_id=vehicle.id, registration_type="NEW",
                  registration_date=date(2026, 6, 1), user=user)


def test_renewal_requires_existing_registration(db, env):
    vehicle, user = env
    svc = VehicleRegistrationService()
    with pytest.raises(NoExistingRegistrationError):
        svc.create(vehicle_id=vehicle.id, registration_type="RENEWAL",
                  registration_date=date(2026, 1, 1), user=user)


def test_renewal_defaults_1_year_validity(db, env):
    vehicle, user = env
    svc = VehicleRegistrationService()
    first = svc.create(vehicle_id=vehicle.id, registration_type="NEW",
                       registration_date=date(2026, 1, 1), user=user)
    svc.submit(first.id, user=user)
    svc.complete(first.id, or_number="OR-1", cr_number="CR-1",
                plate_number="ABC-123")
    renewal = svc.create(vehicle_id=vehicle.id, registration_type="RENEWAL",
                        registration_date=date(2029, 1, 1), user=user)
    assert renewal.validity_years == 1
    assert renewal.expiry_date == date(2030, 1, 1)


def test_complete_new_registration_assigns_plate_to_vehicle(db, env):
    vehicle, user = env
    svc = VehicleRegistrationService()
    reg = svc.create(vehicle_id=vehicle.id, registration_type="NEW",
                     registration_date=date(2026, 1, 1), user=user)
    svc.submit(reg.id, user=user)
    svc.complete(reg.id, or_number="OR-100", cr_number="CR-100",
                plate_number="XYZ-789")
    completed = db.session.get(VehicleRegistration, reg.id)
    assert completed.status == "COMPLETED"
    refreshed_vehicle = VehicleService().get_by_id(vehicle.id) \
        if hasattr(VehicleService(), "get_by_id") else None
    from app.modules.master_data.vehicle.models import Vehicle
    refreshed_vehicle = db.session.get(Vehicle, vehicle.id)
    assert refreshed_vehicle.plate_number == "XYZ-789"


def test_get_expiring_registrations(db, env):
    vehicle, user = env
    svc = VehicleRegistrationService()
    reg = svc.create(vehicle_id=vehicle.id, registration_type="NEW",
                     registration_date=date(2026, 1, 1), user=user)
    svc.submit(reg.id, user=user)
    svc.complete(reg.id, or_number="OR-1", cr_number="CR-1",
                plate_number="AAA-111")
    expiring = svc.get_expiring_registrations(
        days_ahead=100000, as_of_date=date(2026, 1, 2))
    assert any(e["vehicle"].id == vehicle.id for e in expiring)
