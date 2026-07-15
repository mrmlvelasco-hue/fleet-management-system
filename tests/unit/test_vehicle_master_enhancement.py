from datetime import date

import pytest

from app.modules.master_data.vehicle.service import (
    VehicleService, DuplicateVehicleError, InvalidVehicleDataError)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-VMENH", name="VM Enhancement Branch")
    vt = VehicleTypeService().create(code="LV-VMENH", name="Light", category="LIGHT")
    return branch, vt


def test_create_with_new_fields(db, env):
    branch, vt = env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="VMENH-000",
        far_number="FAR-001", cr_number="CR-001", mv_file_number="MVF-001",
        remarks="Test remarks", vehicle_body_type="PICKUP",
        displacement="2.8L", component_group="LIGHT-FLEET",
        supplier="ABC Motors", leasing_company="XYZ Leasing",
        top_up_amount=5000, assured_value_current_year=800000,
        delivery_date=date(2024, 1, 15), start_date=date(2024, 1, 20),
        end_date=date(2027, 1, 20),
        insurance_reference_number="INS-REF-001",
        comprehensive_policy_number="COMP-001",
        comprehensive_insurance_provider="Malayan Insurance",
        ctpl_policy_number="CTPL-001", ctpl_insurance_provider="Stronghold",
        lto_office="LTO Manila", has_ctpl=True,
        ctpl_from_date=date(2024, 1, 1), ctpl_to_date=date(2025, 1, 1),
        assignment="PRIMARY", assignment_group_classification="COMPANY_OWNED",
        vehicle_usage="SALES", mr_eds=True, with_vehicle_contract=False)
    assert v.far_number == "FAR-001"
    assert v.vehicle_body_type == "PICKUP"
    assert v.top_up_amount == 5000
    assert v.has_ctpl is True
    assert v.assignment == "PRIMARY"


def test_engine_number_must_be_unique(db, env):
    branch, vt = env
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="VMENH-001",
        engine_number="ENG-DUP-001")
    with pytest.raises(DuplicateVehicleError):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
            branch_id=branch.id, conduction_number="VMENH-002",
            engine_number="ENG-DUP-001")


def test_acquisition_cost_must_be_positive(db, env):
    branch, vt = env
    with pytest.raises(InvalidVehicleDataError):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
            branch_id=branch.id, conduction_number="VMENH-003",
            acquisition_cost=0)
    with pytest.raises(InvalidVehicleDataError):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
            branch_id=branch.id, conduction_number="VMENH-004",
            acquisition_cost=-500)


def test_purchase_date_cannot_be_later_than_delivery_date(db, env):
    branch, vt = env
    with pytest.raises(InvalidVehicleDataError):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
            branch_id=branch.id, conduction_number="VMENH-005",
            acquisition_date=date(2024, 5, 1), delivery_date=date(2024, 4, 1))


def test_purchase_date_before_delivery_date_is_valid(db, env):
    branch, vt = env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="VMENH-006",
        acquisition_date=date(2024, 4, 1), delivery_date=date(2024, 5, 1))
    assert v.delivery_date == date(2024, 5, 1)


def test_insurance_from_date_must_be_before_to_date(db, env):
    branch, vt = env
    with pytest.raises(InvalidVehicleDataError):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
            branch_id=branch.id, conduction_number="VMENH-007",
            ctpl_from_date=date(2024, 6, 1), ctpl_to_date=date(2024, 1, 1))


def test_all_four_insurance_pairs_validated(db, env):
    branch, vt = env
    pairs = [
        ("od_theft_aon_from_date", "od_theft_aon_to_date"),
        ("vtpl_pd_from_date", "vtpl_pd_to_date"),
        ("vtpl_bi_from_date", "vtpl_bi_to_date"),
    ]
    for i, (from_field, to_field) in enumerate(pairs):
        with pytest.raises(InvalidVehicleDataError):
            VehicleService().create(
                vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
                year=2024, branch_id=branch.id,
                conduction_number=f"VMENH-INS-{i}",
                **{from_field: date(2024, 6, 1), to_field: date(2024, 1, 1)})


def test_update_also_validates(db, env):
    branch, vt = env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="VMENH-008")
    with pytest.raises(InvalidVehicleDataError):
        VehicleService().update(v.id, acquisition_cost=-100)


def test_clone_prefills_data_but_blanks_unique_identifiers(db, env):
    branch, vt = env
    original = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="VMENH-CLONE-SRC",
        plate_number="CLONE 123", chassis_number="CHASSIS-CLONE-1",
        engine_number="ENGINE-CLONE-1", color="Red", vehicle_body_type="PICKUP")

    clone_data = VehicleService().get_clone_data(original.id)
    assert clone_data["color"] == "Red"
    assert clone_data["vehicle_body_type"] == "PICKUP"
    assert clone_data["brand"] == "Toyota"
    # Unique identifiers must NOT be copied
    assert "plate_number" not in clone_data or not clone_data["plate_number"]
    assert "conduction_number" not in clone_data or not clone_data["conduction_number"]
    assert "chassis_number" not in clone_data or not clone_data["chassis_number"]
    assert "engine_number" not in clone_data or not clone_data["engine_number"]
