"""pm_compliance(), registration_status() and registrations_expiring_count()
each independently re-ran a full get_all_due_vehicles() pass with NO
cross-request caching at all (2026-11-09 performance audit) -- measured
at 120-220ms each against a 5,000-vehicle fleet, with no sharing even
across two requests a few seconds apart, let alone within one
/dashboard/bootstrap response that touches more than one of them.

PMDueCalculationService.get_all_due_vehicles() and its Registration
twin are already decorated with @request_cached, which dedupes
IDENTICAL-argument calls made within the SAME request -- but
request_cached is deliberately request-scoped (see
app/core/request_cache.py's own docstring on why), so it does nothing
for two separate requests, and nothing for two calls with genuinely
different arguments (exclude_with_open_order True vs False) even
within one request.

The fix here adds a short (12s, matching the TTL dashboard.py's
_shared_due_rows already uses) cross-request cache at the WIDGET level
-- pm_compliance()'s and registration_status()'s own *returned*
{labels, data, colors} dict, and registrations_expiring_count()'s own
returned int -- scoped per (user, branch, company). This is
deliberately NOT inside get_all_due_vehicles() itself, whose own
docstring says results must not be cached across requests: caching one
layer up, where _shared_due_rows already proves the tradeoff is
accepted, leaves that boundary untouched.
"""
import pytest

from app.core.dashboard_analytics_service import DashboardAnalyticsService
from app.core.dashboard_service import DashboardService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-CACHE", name="Cache Branch")
    other_branch = BranchService().create(code="BR-CACHE-2", name="Cache Branch 2")
    vt = VehicleTypeService().create(code="CACHE-LV", name="Cache Light",
                                     category="LIGHT")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="CACHE-001")
    user = User(username="cache_user", email="cache_user@x.com",
               password_hash="x")
    from app.extensions import db as _db
    _db.session.add(user)
    _db.session.commit()
    return dict(branch=branch, other_branch=other_branch, user=user)


def _spy_due_calls(monkeypatch, module_path, attr):
    import importlib
    module = importlib.import_module(module_path)
    original = getattr(module, attr)
    calls = []

    class _Spy:
        def get_all_due_vehicles(self, *a, **kw):
            calls.append((a, kw))
            return original().get_all_due_vehicles(*a, **kw)

    monkeypatch.setattr(module, attr, _Spy)
    return calls


class TestPmComplianceCaching:
    def test_repeated_calls_within_ttl_hit_the_cache(self, db, env, monkeypatch):
        from app.core.maintenance import due_calculation_service as svc_mod
        calls = _spy_due_calls(
            monkeypatch, "app.core.maintenance.due_calculation_service",
            "PMDueCalculationService")
        analytics = DashboardAnalyticsService()
        analytics.pm_compliance.cache.invalidate()
        analytics.pm_compliance(user=env["user"], branch_id=env["branch"].id)
        analytics.pm_compliance(user=env["user"], branch_id=env["branch"].id)
        assert len(calls) == 1

    def test_different_branch_scope_is_not_shared(self, db, env, monkeypatch):
        calls = _spy_due_calls(
            monkeypatch, "app.core.maintenance.due_calculation_service",
            "PMDueCalculationService")
        analytics = DashboardAnalyticsService()
        analytics.pm_compliance.cache.invalidate()
        analytics.pm_compliance(user=env["user"], branch_id=env["branch"].id)
        analytics.pm_compliance(user=env["user"], branch_id=env["other_branch"].id)
        assert len(calls) == 2


class TestRegistrationStatusCaching:
    def test_repeated_calls_within_ttl_hit_the_cache(self, db, env, monkeypatch):
        calls = _spy_due_calls(
            monkeypatch, "app.modules.registration_config.service",
            "RegistrationDueCalculationService")
        analytics = DashboardAnalyticsService()
        analytics.registration_status.cache.invalidate()
        analytics.registration_status(user=env["user"], branch_id=env["branch"].id)
        analytics.registration_status(user=env["user"], branch_id=env["branch"].id)
        assert len(calls) == 1


class TestRegistrationsExpiringCountCaching:
    def test_repeated_calls_within_ttl_hit_the_cache(self, db, env, monkeypatch):
        calls = _spy_due_calls(
            monkeypatch, "app.modules.registration_config.service",
            "RegistrationDueCalculationService")
        dash = DashboardService()
        dash.registrations_expiring_count.cache.invalidate()
        dash.registrations_expiring_count(user=env["user"], branch_id=env["branch"].id)
        dash.registrations_expiring_count(user=env["user"], branch_id=env["branch"].id)
        assert len(calls) == 1

    def test_different_user_is_not_shared(self, db, env, monkeypatch):
        calls = _spy_due_calls(
            monkeypatch, "app.modules.registration_config.service",
            "RegistrationDueCalculationService")
        from app.extensions import db as _db
        other_user = User(username="cache_user2", email="cache_user2@x.com",
                          password_hash="x")
        _db.session.add(other_user)
        _db.session.commit()
        dash = DashboardService()
        dash.registrations_expiring_count.cache.invalidate()
        dash.registrations_expiring_count(user=env["user"])
        dash.registrations_expiring_count(user=other_user)
        assert len(calls) == 2
