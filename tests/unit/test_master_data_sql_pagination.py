"""SQL-level pagination and visibility for Tire/Battery/Driver.

Audit finding (2026-11-08 performance audit, section 2.1): Tire,
Battery, Driver, Vendor and Vehicle all loaded their ENTIRE table into
Python and paginated/filtered there. Vehicle was already fixed
earlier (list_page(), SQL-scoped) -- this gives Tire, Battery and
Driver the same treatment via a shared helper,
UserOrgScopeService.sql_visibility_clause(), extracted from Vehicle's
already-proven translation.

Every test here follows the same shape Vehicle's own parity tests use
(tests/unit/test_api_vehicles.py): the fast SQL path must return
EXACTLY what the slow, trusted Python path (list()) returns. If the
two ever disagree, the paginated list screen would show a different
fleet of tires/batteries/drivers than the dashboard counts -- that is
the one regression this guards hardest against.
"""
import pytest

from app.extensions import db
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.tire.service import TireService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def scoped_env(db):
    manila = BranchService().create(code="BR-MNL-PAGE", name="Manila Page")
    cebu = BranchService().create(code="BR-CEB-PAGE", name="Cebu Page")

    manila_tire = TireService().create(
        serial_number="PAGE-TIRE-MNL1", brand="Bridgestone", size="185/65R15",
        tire_type="RADIAL", branch_id=manila.id, status="IN_STOCK")
    cebu_tire = TireService().create(
        serial_number="PAGE-TIRE-CEB1", brand="Michelin", size="195/65R15",
        tire_type="RADIAL", branch_id=cebu.id, status="MOUNTED")

    manila_battery = BatteryService().create(
        serial_number="PAGE-BATT-MNL1", brand="Motolite", branch_id=manila.id,
        status="IN_STOCK")
    cebu_battery = BatteryService().create(
        serial_number="PAGE-BATT-CEB1", brand="Century", branch_id=cebu.id,
        status="MOUNTED")

    from tests.conftest import tiny_jpeg_file
    manila_driver = DriverService().create(
        employee_number="PAGE-EMP-MNL1", first_name="Juan", last_name="DelaCruz",
        branch_id=manila.id, assignee_type="EMPLOYEE",
        photo_file=tiny_jpeg_file())
    cebu_driver = DriverService().create(
        employee_number="PAGE-EMP-CEB1", first_name="Maria", last_name="Santos",
        branch_id=cebu.id, assignee_type="EMPLOYEE",
        photo_file=tiny_jpeg_file())

    manila_user = User(username="pagescope_manila",
                       email="pagescope_manila@x.com", password_hash="x")
    cebu_user = User(username="pagescope_cebu",
                     email="pagescope_cebu@x.com", password_hash="x")
    unscoped_user = User(username="pagescope_unscoped",
                         email="pagescope_unscoped@x.com", password_hash="x")
    db.session.add_all([manila_user, cebu_user, unscoped_user])
    db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                 branch_id=manila.id)
    UserOrgScopeService().assign(cebu_user.id, scope_type="BRANCH",
                                 branch_id=cebu.id)

    return dict(
        manila=manila, cebu=cebu,
        manila_tire=manila_tire, cebu_tire=cebu_tire,
        manila_battery=manila_battery, cebu_battery=cebu_battery,
        manila_driver=manila_driver, cebu_driver=cebu_driver,
        manila_user=manila_user, cebu_user=cebu_user,
        unscoped_user=unscoped_user,
    )


# ── Tire ─────────────────────────────────────────────────────────────

class TestTireListPage:
    def test_sql_scope_matches_python_scope_for_restricted_user(
            self, db, scoped_env):
        svc = TireService()
        slow = {t.id for t in svc.list(user=scoped_env["manila_user"])}
        rows, total = svc.list_page(
            user=scoped_env["manila_user"], page=1, page_size=500)
        assert {t.id for t in rows} == slow
        assert total == len(slow)
        assert scoped_env["manila_tire"].id in slow
        assert scoped_env["cebu_tire"].id not in slow

    def test_sql_scope_matches_python_scope_for_unscoped_user(
            self, db, scoped_env):
        svc = TireService()
        slow = {t.id for t in svc.list(user=scoped_env["unscoped_user"])}
        rows, total = svc.list_page(
            user=scoped_env["unscoped_user"], page=1, page_size=500)
        assert {t.id for t in rows} == slow
        assert scoped_env["manila_tire"].id in slow
        assert scoped_env["cebu_tire"].id in slow

    def test_pagination_is_applied_in_sql(self, db, scoped_env):
        svc = TireService()
        rows, total = svc.list_page(page=1, page_size=1)
        assert len(rows) == 1
        assert total >= 2

    def test_status_filter(self, db, scoped_env):
        svc = TireService()
        rows, total = svc.list_page(status="MOUNTED", page=1, page_size=500)
        assert {r.id for r in rows} == {scoped_env["cebu_tire"].id}
        assert total == 1

    def test_branch_filter(self, db, scoped_env):
        svc = TireService()
        rows, total = svc.list_page(
            branch_id=scoped_env["manila"].id, page=1, page_size=500)
        assert {r.id for r in rows} == {scoped_env["manila_tire"].id}

    def test_search_matches_serial_or_brand(self, db, scoped_env):
        svc = TireService()
        rows, total = svc.list_page(search="Michelin", page=1, page_size=500)
        assert {r.id for r in rows} == {scoped_env["cebu_tire"].id}

    def test_no_user_returns_everything(self, db, scoped_env):
        svc = TireService()
        rows, total = svc.list_page(page=1, page_size=500)
        ids = {r.id for r in rows}
        assert scoped_env["manila_tire"].id in ids
        assert scoped_env["cebu_tire"].id in ids


# ── Battery ──────────────────────────────────────────────────────────

class TestBatteryListPage:
    def test_sql_scope_matches_python_scope_for_restricted_user(
            self, db, scoped_env):
        svc = BatteryService()
        slow = {b.id for b in svc.list(user=scoped_env["manila_user"])}
        rows, total = svc.list_page(
            user=scoped_env["manila_user"], page=1, page_size=500)
        assert {b.id for b in rows} == slow
        assert scoped_env["manila_battery"].id in slow
        assert scoped_env["cebu_battery"].id not in slow

    def test_pagination_is_applied_in_sql(self, db, scoped_env):
        svc = BatteryService()
        rows, total = svc.list_page(page=1, page_size=1)
        assert len(rows) == 1
        assert total >= 2

    def test_status_filter(self, db, scoped_env):
        svc = BatteryService()
        rows, total = svc.list_page(status="MOUNTED", page=1, page_size=500)
        assert {r.id for r in rows} == {scoped_env["cebu_battery"].id}


# ── Driver ───────────────────────────────────────────────────────────

class TestDriverListPage:
    def test_sql_scope_matches_python_scope_for_restricted_user(
            self, db, scoped_env):
        svc = DriverService()
        slow = {d.id for d in svc.list(user=scoped_env["manila_user"])}
        rows, total = svc.list_page(
            user=scoped_env["manila_user"], page=1, page_size=500)
        assert {d.id for d in rows} == slow
        assert scoped_env["manila_driver"].id in slow
        assert scoped_env["cebu_driver"].id not in slow

    def test_pagination_is_applied_in_sql(self, db, scoped_env):
        svc = DriverService()
        rows, total = svc.list_page(page=1, page_size=1)
        assert len(rows) == 1
        assert total >= 2

    def test_search_matches_name_or_employee_number(self, db, scoped_env):
        svc = DriverService()
        rows, total = svc.list_page(search="Santos", page=1, page_size=500)
        assert {r.id for r in rows} == {scoped_env["cebu_driver"].id}


# ── Shared helper itself ────────────────────────────────────────────

def test_sql_visibility_clause_returns_none_when_unrestricted(db, scoped_env):
    clause = UserOrgScopeService().sql_visibility_clause(
        scoped_env["unscoped_user"].id, TireService().__class__
        and __import__("app.modules.master_data.tire.models",
                       fromlist=["Tire"]).Tire)
    assert clause is None


def test_sql_visibility_clause_restricts_by_branch(db, scoped_env):
    from app.modules.master_data.tire.models import Tire
    clause = UserOrgScopeService().sql_visibility_clause(
        scoped_env["manila_user"].id, Tire)
    assert clause is not None
    ids = {t.id for t in Tire.query.filter(clause).all()}
    assert scoped_env["manila_tire"].id in ids
    assert scoped_env["cebu_tire"].id not in ids
