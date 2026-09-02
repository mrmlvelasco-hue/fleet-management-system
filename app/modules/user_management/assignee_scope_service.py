"""Assignment-based scope: "which vehicles are THIS person's".

Deliberately narrower than UserOrgScopeService, and it must stay
narrower.

Org scope answers "which vehicles may this ROLE see". For a Fleet
Officer that is correctly the whole branch, and nothing here changes
that. This service answers a different question -- "which vehicles is
this PERSON responsible for" -- which for a driver is one or two.

Finding J1 in the mobile analysis report is precisely the two questions
being confused: vehicle endpoints answer the first one, so a driver with
`vehicle.view` can read every vehicle in their branch and download its
OR and CR. Phase 2 closes that by scoping the new /api/v1/my/* endpoints
on THIS service.

So the one thing this must never do is fall back to org scope when it
finds nothing. Returning "no vehicles" for an unlinked user is the
correct, safe answer. Quietly widening to the branch would reintroduce
J1 through the very service written to prevent it -- and it would do so
invisibly, because every screen would still look right.

Read-only by design. Assignments are written through
VehicleAssignmentService; this only reads them.

Wired to no endpoint yet -- that is Phase 2, on purpose, so the scope
logic can be proven before anything depends on it.
"""
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.vehicle.assignment_models import VehicleAssignment


class AssigneeScopeService:

    def assignee_for(self, user):
        """The Driver record this login belongs to, or None.

        None is an ordinary answer, not an error: most users are not
        assignees, and every caller here treats "not an assignee" as
        "assigned to nothing".
        """
        if user is None or not getattr(user, "id", None):
            return None
        if not getattr(user, "is_active", True):
            return None
        return Driver.query.filter_by(user_id=user.id).first()

    def assigned_vehicle_ids(self, user):
        """Vehicle ids currently held by this user.

        Empty list when the user is unlinked, inactive, or holds
        nothing. NOT a fallback to org scope -- see the module
        docstring.
        """
        assignee = self.assignee_for(user)
        if assignee is None:
            return []
        rows = (VehicleAssignment.query
                .filter_by(driver_id=assignee.id, assigned_to=None)
                .all())
        return [r.vehicle_id for r in rows]

    def covers_vehicle(self, user, vehicle_id) -> bool:
        """True only if this vehicle is CURRENTLY assigned to this user.

        A closed assignment is not coverage: someone who handed a
        vehicle back last month must not keep reading its documents.
        """
        if not vehicle_id:
            return False
        return vehicle_id in self.assigned_vehicle_ids(user)
