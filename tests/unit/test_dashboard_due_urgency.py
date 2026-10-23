"""Dashboard lower cards (2026-10-23): urgency order and overdue counts.

1. Vehicles Due for Maintenance must be ordered by urgency. The
   dashboard's shared due rows only put OVERDUE ahead of DUE_SOON;
   inside the overdue group the order was whatever the calculation
   happened to yield -- the client's screenshot showed 25,000 km over,
   then 10,200 km over, then 41 days overdue, then 25,320 km over. The
   dashboard card shows the top five, so the five most urgent must
   come first. The ranking reused is the one PMDueCalculationService
   already applies to a vehicle's own items (status, then the smallest
   km/day gap) -- not a second definition of urgency.

2. The For Your Action List splits overdue from due-soon. The client
   holds at most 200 maintenance rows and only five registration rows,
   so it cannot count overdue items itself; the server reports the
   count next to the existing total.
"""
import json
from datetime import date, timedelta

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.maintenance_config.models import PMSchedule
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    role = Role(name="Dash Urgency")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "vehicle.view", "reportpmscompliance.view",
        "reportregistrationexpiry.view"])).all()
    user = User(username="urg", email="urg@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])

    branch = BranchService().create(code="BR-URG", name="Urgency Branch")
    vt = VehicleTypeService().create(code="LV-URG", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="URG-PM", name="PM Service",
                                         category="PM")
    _db.session.add(PMSchedule(maintenance_type_id=mt.id, trigger_mode="KM",
                               interval_km=1000, cumulative_km=1000,
                               vehicle_type_id=vt.id))
    _db.session.commit()

    def vehicle(plate, odo, expiry=None):
        v = VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
            branch_id=branch.id, conduction_number=f"C-{plate}",
            plate_number=plate, current_odometer=odo)
        if expiry is not None:
            v.last_known_registration_expiry = expiry
        return v

    # Created least-urgent first, so creation order cannot pass the test.
    vehicle("URG-200", 1200, expiry=date.today() + timedelta(days=10))
    vehicle("URG-1500", 2500, expiry=date.today() - timedelta(days=5))
    vehicle("URG-4000", 5000)
    _db.session.commit()

    from app.modules.api.dashboard import _DUE_CACHE
    _DUE_CACHE.clear()
    return user


def _bootstrap(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "urg", "password": "secret123"})
    token = json.loads(r.get_data(as_text=True))["access_token"]
    r = client.get("/api/v1/dashboard/bootstrap?limit=200",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return json.loads(r.get_data(as_text=True))


def test_overdue_maintenance_is_ordered_most_urgent_first(db, client, env):
    body = _bootstrap(client)
    plates = [r["plate_number"] for r in body["due_maintenance"]["items"]
              if r["status"] == "OVERDUE"]
    assert plates == ["URG-4000", "URG-1500", "URG-200"]


def test_maintenance_payload_reports_the_overdue_count(db, client, env):
    body = _bootstrap(client)
    dm = body["due_maintenance"]
    assert dm["overdue"] == sum(
        1 for r in dm["items"] if r["status"] == "OVERDUE")
    assert dm["overdue"] == 3
    assert dm["total"] == len(dm["items"])


def test_registration_payload_reports_the_overdue_count(db, client, env):
    body = _bootstrap(client)
    reg = body["due_registration"]
    assert reg["total"] == 2
    assert reg["overdue"] == 1
    # Overdue first, then the soonest expiry.
    assert [i["plate_number"] for i in reg["items"]] == ["URG-1500", "URG-200"]


def test_the_shared_urgency_rule_is_one_function():
    """The dashboard and a vehicle's own due list rank by the same rule:
    status first, then the smallest gap (days count as ~50 km)."""
    from app.core.maintenance.due_calculation_service import urgency_key

    today = date(2026, 10, 23)
    far_over = urgency_key("OVERDUE", next_due_km=1000, next_due_date=None,
                           odometer=26000, as_of_date=today)
    near_over = urgency_key("OVERDUE", next_due_km=1000, next_due_date=None,
                            odometer=1200, as_of_date=today)
    days_over = urgency_key("OVERDUE", next_due_km=None,
                            next_due_date=today - timedelta(days=41),
                            odometer=0, as_of_date=today)
    due_soon = urgency_key("DUE_SOON", next_due_km=900, next_due_date=None,
                           odometer=800, as_of_date=today)
    assert sorted([due_soon, near_over, days_over, far_over]) == [
        far_over, days_over, near_over, due_soon]
