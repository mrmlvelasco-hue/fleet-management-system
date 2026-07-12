import pytest

from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService,
    DuplicateBrandError, DuplicateModelError)
from app.modules.master_data.vehicle_brand.models import (
    VehicleBrand, VehicleModel)


def test_create_brand(db):
    brand = VehicleBrandService().create(name="Toyota")
    assert brand.id is not None
    assert brand.name == "Toyota"


def test_duplicate_brand_name_rejected(db):
    VehicleBrandService().create(name="Honda")
    with pytest.raises(DuplicateBrandError, match="already exists"):
        VehicleBrandService().create(name="Honda")


def test_duplicate_brand_name_case_insensitive(db):
    VehicleBrandService().create(name="Toyota")
    with pytest.raises(DuplicateBrandError):
        VehicleBrandService().create(name="toyota")


def test_create_model_under_brand(db):
    brand = VehicleBrandService().create(name="Toyota")
    model = VehicleModelService().create(brand_id=brand.id, name="Hilux")
    assert model.id is not None
    assert model.brand_id == brand.id


def test_duplicate_model_under_same_brand_rejected(db):
    brand = VehicleBrandService().create(name="Toyota")
    VehicleModelService().create(brand_id=brand.id, name="Vios")
    with pytest.raises(DuplicateModelError, match="already exists"):
        VehicleModelService().create(brand_id=brand.id, name="Vios")


def test_same_model_name_allowed_under_different_brands(db):
    toyota = VehicleBrandService().create(name="Toyota")
    honda = VehicleBrandService().create(name="Honda")
    m1 = VehicleModelService().create(brand_id=toyota.id, name="City")
    m2 = VehicleModelService().create(brand_id=honda.id, name="City")
    assert m1.id != m2.id


def test_list_models_by_brand(db):
    toyota = VehicleBrandService().create(name="Toyota")
    honda = VehicleBrandService().create(name="Honda")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux")
    VehicleModelService().create(brand_id=toyota.id, name="Vios")
    VehicleModelService().create(brand_id=honda.id, name="City")
    toyota_models = VehicleModelService().list(brand_id=toyota.id)
    assert {m.name for m in toyota_models} == {"Hilux", "Vios"}


def test_get_by_name_case_insensitive(db):
    VehicleBrandService().create(name="Toyota")
    found = VehicleBrandService().get_by_name("toyota")
    assert found is not None
    assert found.name == "Toyota"


def test_get_by_name_returns_none_when_missing(db):
    assert VehicleBrandService().get_by_name("Nonexistent") is None


def test_model_get_by_name_and_brand(db):
    brand = VehicleBrandService().create(name="Toyota")
    VehicleModelService().create(brand_id=brand.id, name="Hilux")
    found = VehicleModelService().get_by_name_and_brand("hilux", brand.id)
    assert found is not None
    assert found.name == "Hilux"
