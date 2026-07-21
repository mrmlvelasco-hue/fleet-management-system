"""Performance regression tests, for multi-user load concerns raised
directly: N+1 query fixes on the busiest list pages (Vehicle list, MO
list), and the database connection pool configuration (pool_recycle/
pool_pre_ping fix the classic Flask+MySQL "server has gone away" failure
under real concurrent traffic; must stay conditional on dialect so a
SQLite fallback -- dev or test -- never breaks).
"""
from datetime import date

import pytest
from sqlalchemy import event

from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.master_data.reference.service import MaintenanceTypeService


def _count_queries(engine, fn):
    count = {"n": 0}
    def _listener(conn, cursor, statement, parameters, context, executemany):
        count["n"] += 1
    event.listen(engine, "before_cursor_execute", _listener)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _listener)
    return count["n"]


@pytest.fixture()
def branch(db):
    return BranchService().create(code="BR-PERF", name="Perf Branch")


@pytest.fixture()
def vehicle_type(db):
    return VehicleTypeService().create(code="LV-PERF", name="Light",
                                       category="LIGHT")


def test_vehicle_list_does_not_n_plus_one(db, branch, vehicle_type):
    for i in range(5):
        VehicleService().create(
            vehicle_type_id=vehicle_type.id, brand="Toyota", model="Hilux",
            year=2024, branch_id=branch.id, conduction_number=f"PERF-{i}")

    from app.extensions import db as _db

    def _run():
        vehicles = VehicleService().list(include_inactive=True)
        for v in vehicles:
            _ = v.vehicle_type.name if v.vehicle_type else None
            _ = v.branch.name if v.branch else None

    n = _count_queries(_db.engine, _run)
    # One query for the list itself, joined -- must not scale with row
    # count (would be 1 + 2*5 = 11 without eager loading).
    assert n <= 2, f"expected ~1 query with eager loading, got {n}"


def test_maintenance_order_list_does_not_n_plus_one(
        db, branch, vehicle_type):
    mt = MaintenanceTypeService().create(code="PMS-PERF", name="PMS",
                                         category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vehicle_type.id, brand="Ford", model="Escape",
        year=2020, branch_id=branch.id, conduction_number="PERF-MO-0")
    for i in range(5):
        MaintenanceOrderService().create(
            vehicle_id=vehicle.id, maintenance_type_id=mt.id,
            scheduled_date=date.today(), user=None)

    from app.extensions import db as _db

    def _run():
        orders = MaintenanceOrderService().list()
        for o in orders:
            _ = o.vehicle.plate_number if o.vehicle else None
            _ = o.maintenance_type.name if o.maintenance_type else None
            _ = o.transaction_type.name if o.transaction_type else None

    n = _count_queries(_db.engine, _run)
    # Would be 1 + 3*5 = 16 without eager loading.
    assert n <= 2, f"expected ~1 query with eager loading, got {n}"


# ── Connection pool configuration ───────────────────────────────────────────

def test_mysql_uri_gets_full_pool_options():
    from app.config import _build_engine_options
    opts = _build_engine_options(
        "mysql+pymysql://u:p@localhost:3306/db?charset=utf8mb4")
    assert opts["pool_pre_ping"] is True
    assert "pool_recycle" in opts
    assert "pool_size" in opts
    assert "max_overflow" in opts


def test_sqlite_uri_gets_only_pre_ping_not_queuepool_options():
    """StaticPool/NullPool (SQLite) reject pool_size/max_overflow
    outright -- this must never regress, or every SQLite fallback (local
    dev, and the whole test suite) breaks at create_engine()."""
    from app.config import _build_engine_options
    opts = _build_engine_options("sqlite:///fms_dev.db")
    assert opts == {"pool_pre_ping": True}

    opts_memory = _build_engine_options("sqlite://")
    assert opts_memory == {"pool_pre_ping": True}
