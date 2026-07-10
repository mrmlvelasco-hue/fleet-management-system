import pytest
from app.modules.master_data.org.service import (
    BranchService, DepartmentService, BusinessUnitService,
    DuplicateCodeError)
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)


# ---------- Branch ----------

def test_create_branch(db):
    svc = BranchService()
    b = svc.create(code="HQ", name="Head Office", city="Manila")
    assert b.id is not None and b.code == "HQ"


def test_duplicate_branch_code_rejected(db):
    svc = BranchService()
    svc.create(code="MKT", name="Makati")
    with pytest.raises(DuplicateCodeError):
        svc.create(code="MKT", name="Makati 2")


def test_deactivate_and_reactivate_branch(db):
    svc = BranchService()
    b = svc.create(code="BGC", name="BGC Branch")
    svc.deactivate(b.id)
    assert not svc.get(b.id).is_active
    svc.reactivate(b.id)
    assert svc.get(b.id).is_active


# ---------- Department ----------

def test_create_department_linked_to_branch(db):
    branch = BranchService().create(code="B1", name="Branch 1")
    dept = DepartmentService().create(
        code="OPS", name="Operations", branch_id=branch.id)
    assert dept.branch_id == branch.id


# ---------- Business Unit ----------

def test_create_business_unit(db):
    bu = BusinessUnitService().create(code="BU1", name="Logistics")
    assert bu.id is not None


# ---------- Vehicle Type ----------

def test_create_vehicle_type(db):
    vt = VehicleTypeService().create(
        code="LV", name="Light Vehicle", category="LIGHT")
    assert vt.category == "LIGHT"


def test_duplicate_vehicle_type_code_rejected(db):
    VehicleTypeService().create(code="HV", name="Heavy", category="HEAVY")
    with pytest.raises(DuplicateCodeError):
        VehicleTypeService().create(code="HV", name="Other", category="HEAVY")


# ---------- Maintenance Type ----------

def test_create_maintenance_type(db):
    mt = MaintenanceTypeService().create(
        code="PMS", name="Preventive Maintenance Service",
        category="PREVENTIVE", interval_km=5000, interval_days=90)
    assert mt.interval_km == 5000
