"""MO/transaction impact of the Branch->Company cutover on Tire/Battery
auto-enrollment (2026-10-30).

Michael, Batch 3: "please check the impact of this in our MO and
transaction." Found while checking: the Maintenance Order equipment flow
and the Tire/Battery REPLACE transaction both auto-register a brand-new
Tire/Battery record when the workshop supplies one, or register a
never-enrolled one being removed -- and every one of those call sites
stamped only `branch_id=order.vehicle.branch_id` (or `txn.vehicle.branch_id`),
never Company.

That was harmless while every vehicle had a Branch. Now that a vehicle
can be Company-only (Batch 1: Branch removed from the Vehicle form), an
MO or transaction run against such a vehicle would auto-create a Tire or
Battery with NEITHER Branch nor Company -- exactly the anchorless record
UserOrgScopeService.covers() treats as visible to every user, same class
of bug the org_anchor_error rule exists to prevent on direct creation.
These records are created by ORM calls inside equipment.py / the
transaction services, bypassing TireService.create() / BatteryService
.create() (and so the API's org_anchor_error) entirely -- the only fix
available at these call sites is to stamp Company itself.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.battery.models import Battery
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.org.service import (
    RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.tire.models import Tire
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.transactions.battery_txn.service import (
    BatteryTransactionService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    region = RegionService().create(code="NL-EQC", name="North Luzon")
    area = AreaDistrictService().create(code="NL2-EQC", name="NORTH LUZON 2",
                                        region_id=region.id)
    co = OrgCompanyService().create(short_name="Excellence",
                                    region_id=region.id, area_district_id=area.id)
    vt = VehicleTypeService().create(code="LV-EQC", name="Light",
                                     category="LIGHT")
    # Company-only vehicle -- no Branch at all, per Batch 1.
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2021,
        company_id=co.id, conduction_number="EQC-001",
        plate_number="TIL-121")
    vendor = VendorService().create(code="VEN-EQC", name="Gulf Tire Center")
    tire_type = MaintenanceTypeService().create(
        code="PMS-TIRE-EQC", name="Tire Replacement", category="PREVENTIVE",
        equipment_kind="TIRE")
    batt_type = MaintenanceTypeService().create(
        code="PMS-BATT-EQC", name="Battery Replacement", category="PREVENTIVE",
        equipment_kind="BATTERY")
    user = User(username="eqc_user", email="eqc@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()
    for code, name in [("TIR", "Tire Transaction"),
                       ("BAT", "Battery Transaction")]:
        from app.modules.document_config.models import DocumentType
        if DocumentType.query.filter_by(code=code).first() is None:
            dt = DocumentTypeService().create(code=code, name=name,
                                              requires_approval=True,
                                              auto_numbering=True)
            NumberingSchemeService().create(
                document_type_id=dt.id, prefix=code, include_year=True,
                digit_count=6, reset_policy="YEARLY")
    fl_tire = TireService().create(serial_number="BS-EQC-FL", brand="Bridgestone",
                                   size="185/60 R15", tire_type="RADIAL")
    fl_tire.status, fl_tire.vehicle_id, fl_tire.wheel_position = (
        "MOUNTED", vehicle.id, "FL")
    old_battery = BatteryService().create(serial_number="MT-EQC-1",
                                          brand="Motolite")
    old_battery.status, old_battery.vehicle_id = "MOUNTED", vehicle.id
    _db.session.commit()
    return dict(vehicle=vehicle, co=co, vendor=vendor, user=user,
               fl_tire=fl_tire, old_battery=old_battery,
               tire_type=tire_type, batt_type=batt_type)


def _mo(env, mtype, **kw):
    return MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, scheduled_date=date(2026, 10, 15),
        user=env["user"], maintenance_type_id=env[mtype].id,
        vendor_id=env["vendor"].id, odometer_at_service=27000, **kw)


def test_mo_replace_registers_a_vendor_tire_with_the_vehicles_company(
        db, env):
    """equipment.py's _installed_tire: a brand-new vendor tire, auto-
    registered on MO completion, must inherit the Company-only vehicle's
    Company -- not be left anchorless."""
    mo = _mo(env, "tire_type", equipment_lines=[{
        "kind": "TIRE", "position": "FL", "action": "REPLACE",
        "current_tire_id": env["fl_tire"].id, "status": "DONE",
        "install_source": "VENDOR", "new_serial": "DOT-EQC-NEW",
        "new_brand": "Bridgestone", "new_size": "185/60 R15",
    }])
    MaintenanceOrderService().complete(
        mo.id, user=env["user"], actual_cost=Decimal("0"),
        completed_date=date(2026, 10, 15))
    new_tire = Tire.query.filter_by(serial_number="DOT-EQC-NEW").first()
    assert new_tire is not None
    assert new_tire.company_id == env["co"].id
    assert new_tire.branch_id is None


def test_mo_replace_registers_a_vendor_battery_with_the_vehicles_company(
        db, env):
    """equipment.py's _installed_battery: same as the tire case."""
    mo = _mo(env, "batt_type", equipment_lines=[{
        "kind": "BATTERY", "action": "REPLACE",
        "current_battery_id": env["old_battery"].id, "status": "DONE",
        "install_source": "VENDOR", "new_serial": "MT-EQC-NEW",
        "new_brand": "Motolite",
    }])
    MaintenanceOrderService().complete(
        mo.id, user=env["user"], actual_cost=Decimal("0"),
        completed_date=date(2026, 10, 15))
    new_battery = Battery.query.filter_by(serial_number="MT-EQC-NEW").first()
    assert new_battery is not None
    assert new_battery.company_id == env["co"].id
    assert new_battery.branch_id is None


def test_tire_transaction_replace_registers_a_vendor_tire_with_company(
        db, env):
    """tire_txn/service.py's _apply_replace, reached directly (not
    through an MO) -- the standalone Tire Transaction screen's own
    REPLACE action."""
    txn = TireTransactionService().create(
        tire_id=None, action="REPLACE", vehicle_id=env["vehicle"].id,
        wheel_position="FR", user=env["user"],
        transaction_date=date(2026, 10, 15), install_source="VENDOR",
        new_serial="DOT-EQC-STANDALONE", new_brand="Bridgestone",
        new_size="185/60 R15", removed_tire_id=None,
        force_completed=True)
    new_tire = Tire.query.filter_by(
        serial_number="DOT-EQC-STANDALONE").first()
    assert new_tire is not None
    assert new_tire.company_id == env["co"].id
    assert new_tire.branch_id is None


def test_battery_transaction_replace_registers_a_vendor_battery_with_company(
        db, env):
    """battery_txn/service.py's _apply_replace, same as the tire case."""
    txn = BatteryTransactionService().create(
        battery_id=None, action="REPLACE", vehicle_id=env["vehicle"].id,
        user=env["user"], transaction_date=date(2026, 10, 15),
        install_source="VENDOR",
        new_serial="MT-EQC-STANDALONE", new_brand="Motolite",
        removed_battery_id=None, force_completed=True)
    new_battery = Battery.query.filter_by(
        serial_number="MT-EQC-STANDALONE").first()
    assert new_battery is not None
    assert new_battery.company_id == env["co"].id
    assert new_battery.branch_id is None
