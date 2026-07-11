import pytest

from app.modules.system_admin.services.lookup_service import (
    sync_lookups, registry as lookup_registry, LookupService)
from app.modules.transactions.vehicle_movement.service import (
    VehicleMovementService, InvalidMovementTypeError)
from app.modules.master_data.vendor.service import VendorService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User


def test_new_lookup_types_registered_and_seeded(db):
    # Importing routes triggers the module-level registry.register() calls;
    # app factory already does this at startup, so just sync + check.
    sync_lookups()
    db.session.commit()
    for lookup_type, expected_code in [
        ("VEHICLE_CATEGORY", "LIGHT"),
        ("VENDOR_TYPE", "GOODS"),
        ("TIRE_TYPE", "RADIAL"),
        ("MOVEMENT_TYPE", "TRANSFER"),
        ("PM_PRIORITY", "MEDIUM"),
    ]:
        codes = {i.code for i in LookupService().get_by_type(lookup_type)}
        assert expected_code in codes, f"{lookup_type} missing {expected_code}"


def test_movement_type_validated_against_lookup_not_hardcoded_set(db):
    sync_lookups()
    db.session.commit()
    branch = BranchService().create(code="BR-LKP", name="Lookup Branch")
    vt = VehicleTypeService().create(code="LV-LKP", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="LKP-000")
    user = User(username="lkp_user", email="lkp@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()
    dt = DocumentTypeService().create(code="VM", name="Vehicle Movement",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="VM",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    # Admin adds a brand-new movement type via Lookup Maintenance...
    LookupService().create("MOVEMENT_TYPE", "LOAN", "Loaned to another dept",
                          sort_order=5)

    # ...and it should now be accepted without any code change.
    from datetime import date
    mv = VehicleMovementService().create(
        vehicle_id=vehicle.id, movement_type="LOAN",
        from_location="HQ", to_location="Branch B",
        movement_date=date(2026, 7, 15), user=user)
    assert mv.movement_type == "LOAN"

    with pytest.raises(InvalidMovementTypeError):
        VehicleMovementService().create(
            vehicle_id=vehicle.id, movement_type="TELEPORT",
            from_location="HQ", to_location="Mars",
            movement_date=date(2026, 7, 15), user=user)
