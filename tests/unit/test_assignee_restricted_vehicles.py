"""Assignee-restricted vehicle visibility (closes J1 on the web list).

The mobile /api/v1/my/* endpoints were scoped on AssigneeScopeService in
Phase 2, but the WEB vehicle list was not: it filters on org scope
alone. So a Vehicle Assignee whose branch scope covers East Asia
Laboratories saw all four of that branch's vehicles -- including two
unassigned ones and one held by somebody else -- and could open their
detail, documents and work orders.

Org scope answers "which vehicles may this ROLE see" (for a Fleet
Officer, correctly the whole branch). This answers "which vehicles is
this PERSON responsible for". The two must INTERSECT, not replace one
another, and the restriction is opt-in per user so that enabling it can
never silently narrow an existing Fleet Officer's access.
"""
import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.vehicle.assignment_models import VehicleAssignment
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Role, User, UserOrgScope


@pytest.fixture()
def fleet(db, app):
    """Luis's actual situation, reduced to its essentials.

    Two branches; four vehicles in his own branch, exactly one of which
    is currently his.
    """
    sync_permissions()
    from app.modules.master_data.org.models import Branch
    from app.modules.master_data.reference.models import VehicleType
    vtype = VehicleType(code="CARL", name="Car Light", category="CAR")
    db.session.add(vtype)
    db.session.flush()
    east = Branch(name="East Asia Laboratories Inc.", code="EAL")
    hq = Branch(name="Head Office", code="HO")
    db.session.add_all([east, hq])
    db.session.flush()

    luis = User(username="lbautista", email="l@e.com",
                password_hash=hash_password("secret123"), is_active=True,
                branch_id=east.id)
    luis.roles = [Role(name="Vehicle Assignee")]
    officer = User(username="officer", email="o@e.com",
                   password_hash=hash_password("secret123"), is_active=True)
    officer.roles = [Role(name="Fleet Officer")]
    db.session.add_all([luis, officer])
    db.session.flush()

    driver = Driver(first_name="Luis", last_name="Bautista",
                    employee_number="EMP-0001", user_id=luis.id,
                    branch_id=east.id, is_active=True)
    db.session.add(driver)
    db.session.flush()

    mine = Vehicle(plate_number="NZO-619", brand="Toyota", model="Vios",
                   vehicle_type_id=vtype.id, year=2009, branch_id=east.id, is_active=True, status="ACTIVE")
    others = Vehicle(plate_number="TEST-CLN01", brand="Toyota", model="Vios",
                     vehicle_type_id=vtype.id, year=2009, branch_id=east.id, is_active=True, status="ACTIVE")
    spare = Vehicle(plate_number="POI-309", brand="Mitsubishi", model="Lancer",
                    vehicle_type_id=vtype.id, year=2009, branch_id=east.id, is_active=True, status="ACTIVE")
    hq_car = Vehicle(plate_number="NIP-232", brand="Mitsubishi", model="Lancer",
                     vehicle_type_id=vtype.id, year=2009, branch_id=hq.id, is_active=True, status="ACTIVE")
    db.session.add_all([mine, others, spare, hq_car])
    db.session.flush()

    # Open assignment: assigned_to IS NULL is what makes it current.
    db.session.add(VehicleAssignment(vehicle_id=mine.id, driver_id=driver.id,
                                     assigned_to=None, source="ATD"))
    # A CLOSED one, for the vehicle he handed back.
    db.session.add(VehicleAssignment(
        vehicle_id=spare.id, driver_id=driver.id,
        assigned_from=None, assigned_to=__import__("datetime").date(2026, 1, 1),
        source="ATD"))
    db.session.add(UserOrgScope(user_id=luis.id, scope_type="BRANCH",
                                branch_id=east.id, is_active=True))
    db.session.commit()
    return {"luis": luis, "officer": officer, "east": east, "hq": hq,
            "mine": mine, "others": others, "spare": spare, "hq_car": hq_car}


def _plates(rows):
    return sorted(v.plate_number for v in rows)


class TestRestrictedAssignee:
    def test_sees_only_currently_assigned_vehicles(self, fleet, db):
        """The headline requirement."""
        fleet["luis"].restrict_to_assigned_vehicles = True
        db.session.commit()
        rows, total = VehicleService().list_page(user=fleet["luis"],
                                                 page_size=50)
        assert _plates(rows) == ["NZO-619"]
        assert total == 1

    def test_a_returned_vehicle_is_not_visible(self, fleet, db):
        """A closed assignment is not coverage.

        Someone who handed a vehicle back last month must not keep
        reading its documents -- the rule AssigneeScopeService already
        applies for mobile, asserted here so the web path cannot drift
        from it.
        """
        fleet["luis"].restrict_to_assigned_vehicles = True
        db.session.commit()
        rows, _ = VehicleService().list_page(user=fleet["luis"], page_size=50)
        assert "POI-309" not in _plates(rows)

    def test_branch_scope_still_applies_on_top(self, fleet, db):
        """INTERSECTION, not replacement.

        A vehicle assigned to him in a branch his scope does NOT cover
        must still be hidden, or assignment would become a way around
        org scope.
        """
        fleet["luis"].restrict_to_assigned_vehicles = True
        driver = Driver.query.filter_by(user_id=fleet["luis"].id).first()
        db.session.add(VehicleAssignment(vehicle_id=fleet["hq_car"].id,
                                         driver_id=driver.id,
                                         assigned_to=None, source="MANUAL"))
        db.session.commit()
        rows, _ = VehicleService().list_page(user=fleet["luis"], page_size=50)
        assert "NIP-232" not in _plates(rows)

    def test_detail_is_refused_for_an_unassigned_vehicle(self, fleet, db):
        """The list is not the only door.

        Hiding a row while get_visible() still returns the record would
        leave the vehicle reachable by typing its id into the URL --
        which is exactly how J1 was originally reachable.
        """
        fleet["luis"].restrict_to_assigned_vehicles = True
        db.session.commit()
        svc = VehicleService()
        assert svc.get_visible(fleet["mine"].id, fleet["luis"]) is not None
        assert svc.get_visible(fleet["others"].id, fleet["luis"]) is None

    def test_creating_a_vehicle_does_not_keep_it_visible(self, fleet, db):
        """Mutation guard on the ORDER of checks in get_visible().

        The restriction must be evaluated BEFORE the created_by
        allowance. Reordering them leaves a restricted assignee able to
        open any vehicle whose record they happened to enter -- by
        typing its id into the URL -- long after it became someone
        else's to drive. Every other test passed with the checks
        swapped; only this one fails.
        """
        fleet["luis"].restrict_to_assigned_vehicles = True
        fleet["others"].created_by = fleet["luis"].id
        db.session.commit()
        svc = VehicleService()
        assert svc.get_visible(fleet["others"].id, fleet["luis"]) is None
        rows, _ = svc.list_page(user=fleet["luis"], page_size=50)
        assert "TEST-CLN01" not in _plates(rows)

    def test_list_and_list_page_agree(self, fleet, db):
        """The two paths must return the same ids.

        The dashboard counts through list() and the screen pages through
        list_page(); if they disagree the header says one number and the
        table shows another.
        """
        fleet["luis"].restrict_to_assigned_vehicles = True
        db.session.commit()
        svc = VehicleService()
        rows, _ = svc.list_page(user=fleet["luis"], page_size=50)
        assert _plates(rows) == _plates(svc.list(user=fleet["luis"]))

    def test_an_unlinked_restricted_user_sees_nothing(self, fleet, db):
        """No Driver record means no assignments means no vehicles.

        Empty is the SAFE answer. Falling back to org scope here would
        reintroduce J1 through the very flag meant to prevent it.
        """
        fleet["officer"].restrict_to_assigned_vehicles = True
        db.session.commit()
        rows, total = VehicleService().list_page(user=fleet["officer"],
                                                 page_size=50)
        assert rows == [] and total == 0


class TestUnrestrictedUsersAreUnaffected:
    def test_fleet_officer_with_no_scope_still_sees_everything(self, fleet):
        """The Fleet Officer case Michael called out.

        No scope rows and no restriction: global access, including
        vehicles assigned to nobody, so they can still raise a
        Maintenance Order.
        """
        rows, _ = VehicleService().list_page(user=fleet["officer"],
                                             page_size=50)
        assert _plates(rows) == ["NIP-232", "NZO-619", "POI-309", "TEST-CLN01"]

    def test_assignee_without_the_flag_keeps_branch_scope(self, fleet):
        """Default OFF.

        Enabling this feature must change nothing for anyone until an
        administrator opts a specific user in.
        """
        rows, _ = VehicleService().list_page(user=fleet["luis"], page_size=50)
        assert _plates(rows) == ["NZO-619", "POI-309", "TEST-CLN01"]

    def test_flag_defaults_to_false(self, fleet):
        assert bool(fleet["luis"].restrict_to_assigned_vehicles) is False
