"""PM Templates list grouped by profile, and the backfill that makes
grouping possible for series created with a blank Profile Code.

Two pieces, from the approved spec:

1. backfill_missing_profile_codes(): a one-time, additive fix for
   schedules created via the "New PMS Profile" page with Profile Code
   left blank (profile_code IS NULL). Groups them by vehicle match +
   maintenance type + trigger mode and assigns each group a generated
   code: BRAND-MODEL-MTYPECODE-N. Confirmed during design that this
   never touches migrated VEMS data -- scripts/import_pm_task_list.py
   already sets profile_code from the source system's own Task_CD,
   shared correctly across every interval of one real-world profile.

2. list_profiles_paginated(): one row per profile_code group (or per
   standalone schedule, for the rare case profile_code is still NULL
   after backfill -- e.g. a schedule never part of a profile at all).
   Built as two passes specifically to avoid the exact performance
   mistake already documented in list_paginated's own docstring: a
   lightweight (id, profile_code) query drives grouping and pagination
   in Python (cheap -- two scalar columns, not ORM objects), and only
   the current page's handful of representative rows are then loaded
   with their full eager-loaded relationships for JSON output.
"""
import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.maintenance_config.models import PMSchedule
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    vt = VehicleTypeService().create(code="LV-G", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-PREV", name="Preventive",
                                         category="PREVENTIVE")
    brand = VehicleBrandService().create(name="Geely")
    model = VehicleModelService().create(brand_id=brand.id, name="Coolray")
    db.session.commit()
    return {"vt": vt, "mt": mt, "brand": brand, "model": model}


class TestBackfillMissingProfileCodes:
    def test_assigns_one_generated_code_to_a_whole_blank_series(self, db, env):
        PMScheduleService().generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            vehicle_type_id=env["vt"].id, interval_days=180,
            services=[{"cumulative_km": 1000}, {"cumulative_km": 10000},
                     {"cumulative_km": 20000}])
        db.session.commit()
        assert PMSchedule.query.filter_by(profile_code=None).count() == 3

        result = PMScheduleService().backfill_missing_profile_codes()

        rows = PMSchedule.query.all()
        codes = {r.profile_code for r in rows}
        assert len(codes) == 1
        code = codes.pop()
        assert code == "GEELY-COOLRAY-PMS-PREV-1"
        assert result["groups_fixed"] == 1
        assert result["schedules_updated"] == 3

    def test_does_not_touch_a_schedule_that_already_has_a_code(self, db, env):
        PMScheduleService().generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="MIGRATED-CODE-123", interval_days=180,
            services=[{"cumulative_km": 1000}, {"cumulative_km": 10000}])
        db.session.commit()

        PMScheduleService().backfill_missing_profile_codes()

        rows = PMSchedule.query.all()
        assert all(r.profile_code == "MIGRATED-CODE-123" for r in rows)

    def test_two_separate_blank_series_get_two_different_generated_codes(
            self, db, env):
        svc = PMScheduleService()
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            interval_days=180, services=[{"cumulative_km": 1000}])
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="KM",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            services=[{"cumulative_km": 5000}])
        db.session.commit()

        svc.backfill_missing_profile_codes()

        codes = sorted(r.profile_code for r in PMSchedule.query.all())
        assert codes == ["GEELY-COOLRAY-PMS-PREV-1", "GEELY-COOLRAY-PMS-PREV-2"]

    def test_is_safe_to_run_twice(self, db, env):
        PMScheduleService().generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            interval_days=180, services=[{"cumulative_km": 1000}])
        db.session.commit()

        PMScheduleService().backfill_missing_profile_codes()
        first_code = PMSchedule.query.first().profile_code

        result2 = PMScheduleService().backfill_missing_profile_codes()

        assert PMSchedule.query.first().profile_code == first_code
        assert result2["groups_fixed"] == 0


class TestDeactivateWholeProfile:
    def test_deactivates_every_schedule_sharing_the_profile_code(self, db, env):
        svc = PMScheduleService()
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="KM",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="GEELY-COOLRAY-PMS-1",
            services=[{"cumulative_km": k} for k in (1000, 10000, 20000)])
        db.session.commit()

        count = svc.deactivate_profile("GEELY-COOLRAY-PMS-1")

        assert count == 3
        rows = PMSchedule.query.filter_by(profile_code="GEELY-COOLRAY-PMS-1").all()
        assert all(not r.is_active for r in rows)

    def test_does_not_touch_a_different_profile(self, db, env):
        svc = PMScheduleService()
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="KM",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="PROFILE-A", services=[{"cumulative_km": 1000}])
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="KM",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="PROFILE-B", services=[{"cumulative_km": 5000}])
        db.session.commit()

        svc.deactivate_profile("PROFILE-A")

        b = PMSchedule.query.filter_by(profile_code="PROFILE-B").first()
        assert b.is_active is True

    def test_running_it_twice_is_safe_and_reports_zero_the_second_time(
            self, db, env):
        svc = PMScheduleService()
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="KM",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="PROFILE-A", services=[{"cumulative_km": 1000}])
        db.session.commit()

        first = svc.deactivate_profile("PROFILE-A")
        second = svc.deactivate_profile("PROFILE-A")

        assert first == 1
        assert second == 0


class TestGroupedProfileList:
    def test_one_row_per_profile_regardless_of_interval_count(self, db, env):
        PMScheduleService().generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            vehicle_type_id=env["vt"].id, profile_code="GEELY-COOLRAY-PMS-1",
            interval_days=180,
            services=[{"cumulative_km": k} for k in
                     (1000, 10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000)])
        db.session.commit()

        rows, total = PMScheduleService().list_profiles_paginated(page=1, per_page=25)

        assert total == 1
        assert len(rows) == 1
        row, package_count = rows[0]
        assert row.profile_code == "GEELY-COOLRAY-PMS-1"
        assert package_count == 9

    def test_different_profiles_for_the_same_vehicle_stay_separate_rows(
            self, db, env):
        svc = PMScheduleService()
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID", interval_days=180,
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="GEELY-COOLRAY-PMS-1",
            services=[{"cumulative_km": 1000}, {"cumulative_km": 10000}])
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="KM",
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="GEELY-COOLRAY-PMS-2",
            services=[{"cumulative_km": 5000}])
        db.session.commit()

        rows, total = PMScheduleService().list_profiles_paginated(page=1, per_page=25)
        assert total == 2
        codes = {r.profile_code for r, _ in rows}
        assert codes == {"GEELY-COOLRAY-PMS-1", "GEELY-COOLRAY-PMS-2"}

    def test_a_schedule_with_no_profile_code_is_its_own_row_not_merged(
            self, db, env):
        s1 = PMScheduleService().create(
            maintenance_type_id=env["mt"].id, trigger_mode="KM", interval_km=5000)
        s2 = PMScheduleService().create(
            maintenance_type_id=env["mt"].id, trigger_mode="KM", interval_km=5000)
        db.session.commit()
        assert s1.profile_code is None and s2.profile_code is None

        rows, total = PMScheduleService().list_profiles_paginated(page=1, per_page=25)
        assert total == 2  # two standalone rows, never merged just for sharing NULL

    def test_pagination_counts_groups_not_raw_schedule_rows(self, db, env):
        svc = PMScheduleService()
        # One profile with 9 intervals (9 raw rows) + one standalone
        # schedule (1 raw row) = 10 raw rows, but 2 groups.
        svc.generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID", interval_days=180,
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="GEELY-COOLRAY-PMS-1",
            services=[{"cumulative_km": k} for k in
                     (1000, 10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000)])
        svc.create(maintenance_type_id=env["mt"].id, trigger_mode="KM",
                  interval_km=5000)
        db.session.commit()
        assert PMSchedule.query.count() == 10

        rows, total = PMScheduleService().list_profiles_paginated(page=1, per_page=25)
        assert total == 2
        assert len(rows) == 2

    def test_search_matches_the_group_not_just_one_interval(self, db, env):
        PMScheduleService().generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID", interval_days=180,
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="GEELY-COOLRAY-PMS-1",
            services=[{"cumulative_km": 1000}, {"cumulative_km": 10000}])
        db.session.commit()

        rows, total = PMScheduleService().list_profiles_paginated(
            page=1, per_page=25, search="GEELY-COOLRAY-PMS-1")
        assert total == 1

        rows, total = PMScheduleService().list_profiles_paginated(
            page=1, per_page=25, search="no-such-code")
        assert total == 0

    def test_search_by_brand_or_model_name_finds_a_vehicle_matched_via_the_fk_fields(
            self, db, env):
        # The real bug this guards: a series created through the New
        # PMS Profile page picks Brand/Model from dropdowns, which sets
        # vehicle_brand_id/vehicle_model_id -- NOT the free-text
        # vehicle_make/vehicle_model columns the old search only
        # checked. Confirmed against a real report: searching "geely"
        # for a Geely Coolray profile created this way returned zero
        # results, even though the profile genuinely existed.
        PMScheduleService().generate_series(
            maintenance_type_id=env["mt"].id, trigger_mode="HYBRID", interval_days=180,
            vehicle_brand_id=env["brand"].id, vehicle_model_id=env["model"].id,
            profile_code="PROFILE-X-1",  # deliberately contains none of the
            # search terms below -- a pass here must come from the
            # brand/model join, not an accidental profile_code match.
            services=[{"cumulative_km": 1000}, {"cumulative_km": 10000}])
        db.session.commit()

        _rows, total = PMScheduleService().list_profiles_paginated(
            page=1, per_page=25, search="geely")
        assert total == 1, "searching the vehicle's own brand name found nothing"

        _rows, total = PMScheduleService().list_profiles_paginated(
            page=1, per_page=25, search="coolray")
        assert total == 1, "searching the vehicle's own model name found nothing"

        _rows, total = PMScheduleService().list_profiles_paginated(
            page=1, per_page=25, search="toyota")
        assert total == 0