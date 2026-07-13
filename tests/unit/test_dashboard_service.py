from datetime import date

import pytest

from app.core.dashboard_service import DashboardService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    manila = BranchService().create(code="BR-DASH-MNL", name="Manila Dash")
    cebu = BranchService().create(code="BR-DASH-CEB", name="Cebu Dash")
    vt = VehicleTypeService().create(code="LV-DASH", name="Light", category="LIGHT")

    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Vios",
                           year=2024, branch_id=manila.id,
                           conduction_number="DASH-MNL-1")
    VehicleService().create(vehicle_type_id=vt.id, brand="Honda", model="City",
                           year=2024, branch_id=cebu.id,
                           conduction_number="DASH-CEB-1")

    TireService().create(serial_number="TIRE-DASH-MNL", brand="Bridgestone",
                        size="185/65R15", tire_type="RADIAL", branch_id=manila.id)
    TireService().create(serial_number="TIRE-DASH-CEB", brand="Michelin",
                        size="195/65R15", tire_type="RADIAL", branch_id=cebu.id)

    BatteryService().create(serial_number="BATT-DASH-MNL", brand="Motolite",
                           branch_id=manila.id)

    manila_user = User(username="dashscope_manila", email="dashscope_manila@x.com",
                       password_hash="x")
    from app.extensions import db as _db
    _db.session.add(manila_user)
    _db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                branch_id=manila.id)

    return manila, cebu, manila_user


def test_fleet_count_respects_org_scope(db, env):
    manila, cebu, manila_user = env
    svc = DashboardService()
    assert svc.fleet_count(user=manila_user) == 1
    assert svc.fleet_count(user=None) == 2


def test_tire_stock_count_respects_org_scope(db, env):
    manila, cebu, manila_user = env
    svc = DashboardService()
    assert svc.tire_stock_count(user=manila_user) == 1
    assert svc.tire_stock_count(user=None) == 2


def test_battery_stock_count_respects_org_scope(db, env):
    manila, cebu, manila_user = env
    svc = DashboardService()
    assert svc.battery_stock_count(user=manila_user) == 1
    assert svc.battery_stock_count(user=None) == 1


def test_maintenance_due_count_returns_zero_when_no_schedules(db, env):
    manila, cebu, manila_user = env
    svc = DashboardService()
    assert svc.maintenance_due_count(user=manila_user) == 0


def test_approvals_pending_count_for_user(db, env):
    manila, cebu, manila_user = env
    svc = DashboardService()
    assert svc.approvals_pending_count(manila_user) == 0


def test_registrations_expiring_count(db, env):
    manila, cebu, manila_user = env
    svc = DashboardService()
    assert svc.registrations_expiring_count(user=manila_user) == 0
