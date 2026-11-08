"""DashboardService.fleet_count() / tire_stock_count() / battery_stock_count()
used to call VehicleService/TireService/BatteryService's full Python-side
list() just to len() the result -- the exact same load-everything-then-
count anti-pattern already fixed on the Tire/Battery/Driver list screens
(2026-11-08/09 performance audit), just hiding inside the dashboard's KPI
tiles. Measured at ~130-220ms per call against a 5,000-vehicle fleet
with no scope restriction at all -- pure overhead for a number.

These tests pin two things: the returned COUNT must still match the
old, trusted Python-side computation exactly (parity), and the fix must
actually stop hydrating full ORM objects for rows beyond what's needed
to count them (the actual performance claim, not just a coincidentally
equal number).
"""
import pytest

from app.core.dashboard_service import DashboardService
from app.extensions import db
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    manila = BranchService().create(code="BR-MNL-DASH", name="Manila Dash")
    cebu = BranchService().create(code="BR-CEB-DASH", name="Cebu Dash")
    vt = VehicleTypeService().create(code="DASH-LV", name="Light Dash",
                                     category="LIGHT")

    v1 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=manila.id, conduction_number="DASH-001")
    v2 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2023,
        branch_id=cebu.id, conduction_number="DASH-002")

    t1 = TireService().create(serial_number="DASH-TIRE-1", brand="Bridgestone",
                              size="185/65R15", tire_type="RADIAL",
                              branch_id=manila.id, status="IN_STOCK")
    t2 = TireService().create(serial_number="DASH-TIRE-2", brand="Michelin",
                              size="195/65R15", tire_type="RADIAL",
                              branch_id=cebu.id, status="IN_STOCK")

    b1 = BatteryService().create(serial_number="DASH-BATT-1", brand="Motolite",
                                 branch_id=manila.id, status="IN_STOCK")
    b2 = BatteryService().create(serial_number="DASH-BATT-2", brand="Century",
                                 branch_id=cebu.id, status="IN_STOCK")

    manila_user = User(username="dashscope_manila",
                       email="dashscope_manila@x.com", password_hash="x")
    db.session.add(manila_user)
    db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                 branch_id=manila.id)

    return dict(manila=manila, cebu=cebu, v1=v1, v2=v2, t1=t1, t2=t2,
               b1=b1, b2=b2, manila_user=manila_user)


def test_fleet_count_matches_python_list_unscoped(db, env):
    dash = DashboardService()
    expected = len(VehicleService().list(include_inactive=False))
    assert dash.fleet_count() == expected == 2


def test_fleet_count_matches_python_list_scoped(db, env):
    dash = DashboardService()
    user = env["manila_user"]
    expected = len(VehicleService().list(include_inactive=False, user=user))
    assert dash.fleet_count(user=user) == expected == 1


def test_fleet_count_respects_branch_filter(db, env):
    dash = DashboardService()
    assert dash.fleet_count(branch_id=env["cebu"].id) == 1


def test_tire_stock_count_matches_python_list(db, env):
    dash = DashboardService()
    user = env["manila_user"]
    expected = len(TireService().list(status="IN_STOCK", user=user))
    assert dash.tire_stock_count(user=user) == expected == 1


def test_battery_stock_count_matches_python_list(db, env):
    dash = DashboardService()
    user = env["manila_user"]
    expected = len(BatteryService().list(status="IN_STOCK", user=user))
    assert dash.battery_stock_count(user=user) == expected == 1


def test_fleet_count_does_not_hydrate_every_vehicle(db, env):
    """The actual performance claim: counting 2 vehicles must not
    instantiate 2 (or more) full Vehicle ORM objects along the way --
    it should be a bare SQL COUNT. Uses list_page(page_size=1) under
    the hood, so at most ONE row is ever hydrated, regardless of how
    many vehicles exist."""
    from app.modules.master_data.vehicle.models import Vehicle
    db.session.expunge_all()

    from sqlalchemy import event
    hydrated = []
    def _on_load(target, context):
        hydrated.append(target)
    event.listen(Vehicle, "load", _on_load)
    try:
        DashboardService().fleet_count()
    finally:
        event.remove(Vehicle, "load", _on_load)

    assert len(hydrated) <= 1
