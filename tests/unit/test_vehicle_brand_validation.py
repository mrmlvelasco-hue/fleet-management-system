import pytest

from app.modules.master_data.vehicle.service import (
    VehicleService, BrandRequiredError, ModelRequiredError,
    InvalidBrandError, InvalidModelError, ModelBrandMismatchError)
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-BM", name="Brand Model Branch")
    vt = VehicleTypeService().create(code="LV-BM", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="Toyota")
    honda = VehicleBrandService().create(name="Honda")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux")
    VehicleModelService().create(brand_id=honda.id, name="City")
    return branch, vt, toyota, honda


def test_create_with_valid_brand_and_model_strict(db, env):
    branch, vt, toyota, honda = env
    svc = VehicleService()
    vehicle = svc.create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="BM-001", strict=True)
    assert vehicle.brand == "Toyota"
    assert vehicle.model == "Hilux"


def test_missing_brand_raises_friendly_error(db, env):
    branch, vt, toyota, honda = env
    with pytest.raises(BrandRequiredError, match="Brand is required."):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="", model="Hilux", year=2024,
            branch_id=branch.id, conduction_number="BM-002", strict=True)


def test_missing_model_raises_friendly_error(db, env):
    branch, vt, toyota, honda = env
    with pytest.raises(ModelRequiredError, match="Model is required."):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="", year=2024,
            branch_id=branch.id, conduction_number="BM-003", strict=True)


def test_invalid_brand_raises_friendly_error(db, env):
    branch, vt, toyota, honda = env
    with pytest.raises(InvalidBrandError,
                       match="Please select a valid Brand from the master list."):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="NotARealBrand", model="Hilux",
            year=2024, branch_id=branch.id, conduction_number="BM-004",
            strict=True)


def test_invalid_model_raises_friendly_error(db, env):
    branch, vt, toyota, honda = env
    with pytest.raises(InvalidModelError,
                       match="Please select a valid Model from the master list."):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="NotARealModel",
            year=2024, branch_id=branch.id, conduction_number="BM-005",
            strict=True)


def test_model_not_belonging_to_brand_raises_friendly_error(db, env):
    branch, vt, toyota, honda = env
    with pytest.raises(ModelBrandMismatchError,
                       match="Selected Model does not belong to the selected Brand."):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="City",  # City belongs to Honda
            year=2024, branch_id=branch.id, conduction_number="BM-006",
            strict=True)


def test_non_strict_mode_auto_creates_brand_and_model_for_backward_compat(db, env):
    """Backward compatibility: internal/CSV/test callers that don't pass
    strict=True keep working exactly as before (get-or-create), so the
    existing ~40+ test call sites across the suite aren't broken."""
    branch, vt, toyota, honda = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="BrandNewBrand", model="BrandNewModel",
        year=2024, branch_id=branch.id, conduction_number="BM-007")
    assert vehicle.brand == "BrandNewBrand"
    assert vehicle.model == "BrandNewModel"
    # And the master data got backfilled too, for future standardization:
    assert VehicleBrandService().get_by_name("BrandNewBrand") is not None
    assert VehicleModelService().get_by_name_and_brand(
        "BrandNewModel",
        VehicleBrandService().get_by_name("BrandNewBrand").id) is not None
