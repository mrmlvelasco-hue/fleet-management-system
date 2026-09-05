"""A registration cannot be COMPLETED before it is approved.

Reported from the client's own walkthrough: opening a DRAFT registration
showed "Record Issued Documents" with a Complete Registration button,
and pressing it completed the record -- without it ever being submitted,
let alone approved.

That is an approval bypass on a document whose whole point is the
approval: recording the OR/CR is the LAST step, after someone has
authorised the spend.

The rule follows the Document Type, not a hardcoded status list, per
"no values shall be hardcoded" -- if VEHICLEREGISTRATION is configured
as not requiring approval, completing a draft directly is legitimate and
must keep working. Tire transactions already model this correctly; this
module did not.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.document_config.models import DocumentType
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.vehicle_registration.models import (
    VehicleRegistration)
from app.modules.transactions.vehicle_registration.service import (
    RegistrationNotApprovedError, VehicleRegistrationService)
from app.modules.user_management.models import Permission, Role, User


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        db.session.flush()
    return p


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()
    role = Role(name="R", description="r")
    db.session.add(role)
    db.session.flush()
    for c in ("vehicleregistration.view", "vehicleregistration.create",
              "vehicleregistration.update"):
        role.permissions.append(_perm(c))
    u = User(username="admin", email="a@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Vios", year=2009, branch_id=b.id,
                                conduction_number="A1", plate_number="NZO-619")
    db.session.commit()
    return v, u


def _doc_type(requires_approval):
    dt = DocumentType.query.filter_by(code="VEHICLEREGISTRATION").first()
    if dt is None:
        dt = DocumentType(code="VEHICLEREGISTRATION",
                          name="Vehicle Registration",
                          requires_approval=requires_approval)
        db.session.add(dt)
    else:
        dt.requires_approval = requires_approval
    db.session.commit()
    return dt


def _draft(v, u):
    from datetime import date
    reg = VehicleRegistrationService().create(
        vehicle_id=v.id, registration_type="NEW",
        registration_date=date.today(), user=u)
    db.session.commit()
    return reg


def test_a_draft_cannot_be_completed_when_approval_is_required(app, env):
    """THE bug. The client completed a DRAFT that had never been
    submitted -- recording the OR/CR is the last step, after someone has
    authorised the spend."""
    v, u = env
    _doc_type(True)
    reg = _draft(v, u)

    with pytest.raises(RegistrationNotApprovedError):
        VehicleRegistrationService().complete(
            reg.id, or_number="OR-1", cr_number="CR-1")

    assert db.session.get(VehicleRegistration, reg.id).status == "DRAFT"


def test_the_refusal_explains_what_to_do(app, env):
    v, u = env
    _doc_type(True)
    reg = _draft(v, u)
    try:
        VehicleRegistrationService().complete(reg.id, or_number="OR-2",
                                              cr_number="CR-2")
        pytest.fail("should have raised")
    except RegistrationNotApprovedError as e:
        assert "approv" in str(e).lower()


def test_completion_works_once_approved(app, env):
    v, u = env
    _doc_type(True)
    reg = _draft(v, u)
    # Stand in for the engine having approved it.
    reg.status = "APPROVED"
    db.session.commit()

    VehicleRegistrationService().complete(reg.id, or_number="OR-3",
                                          cr_number="CR-3")

    assert db.session.get(VehicleRegistration, reg.id).status == "COMPLETED"


def test_a_document_type_that_needs_no_approval_still_completes(app, env):
    """The rule follows the Document Type, not a hardcoded status list.
    If the client configures registration as not requiring approval,
    completing a draft directly is legitimate."""
    v, u = env
    _doc_type(False)
    reg = _draft(v, u)

    VehicleRegistrationService().complete(reg.id, or_number="OR-4",
                                          cr_number="CR-4")

    assert db.session.get(VehicleRegistration, reg.id).status == "COMPLETED"


def test_a_cancelled_registration_cannot_be_completed(app, env):
    v, u = env
    _doc_type(False)
    reg = _draft(v, u)
    reg.status = "CANCELLED"
    db.session.commit()

    with pytest.raises(RegistrationNotApprovedError):
        VehicleRegistrationService().complete(reg.id, or_number="OR-5",
                                              cr_number="CR-5")


def test_completing_twice_is_refused(app, env):
    """A COMPLETED record re-completed would re-assign the plate and
    overwrite issued document numbers."""
    v, u = env
    _doc_type(False)
    reg = _draft(v, u)
    VehicleRegistrationService().complete(reg.id, or_number="OR-6",
                                          cr_number="CR-6")

    with pytest.raises(RegistrationNotApprovedError):
        VehicleRegistrationService().complete(reg.id, or_number="OR-7",
                                              cr_number="CR-7")


# ── the same bypass in Vehicle Movement ──────────────────────────────

def test_vehicle_movement_cannot_start_transit_before_approval(app, env):
    """Found by checking the other modules after the registration bug.

    start_transit() and complete() set the status from ANY state with no
    approval check -- the same shape as the registration bypass. Trip
    Ticket and ATD both guard correctly; these two did not.

    Moving a vehicle between branches without the movement being
    approved is the real-world consequence.
    """
    from app.modules.transactions.vehicle_movement.service import (
        InvalidMovementStateError, VehicleMovementService)
    from app.modules.document_config.models import DocumentType
    from datetime import date
    v, u = env
    # Configured as requiring approval, which is the case that matters.
    dt = DocumentType.query.filter_by(code="VEHICLEMOVEMENT").first()
    if dt is None:
        dt = DocumentType(code="VEHICLEMOVEMENT", name="Vehicle Movement",
                          requires_approval=True)
        db.session.add(dt)
    else:
        dt.requires_approval = True
    db.session.commit()
    mv = VehicleMovementService().create(
        vehicle_id=v.id, movement_type="TRANSFER",
        from_location="Carmona", to_location="Manila",
        movement_date=date.today(), user=u)
    db.session.commit()

    with pytest.raises(InvalidMovementStateError):
        VehicleMovementService().start_transit(mv.id)


def test_vehicle_movement_cannot_complete_without_approval(app, env):
    from app.modules.transactions.vehicle_movement.service import (
        InvalidMovementStateError, VehicleMovementService)
    from app.modules.document_config.models import DocumentType
    from datetime import date
    v, u = env
    dt = DocumentType.query.filter_by(code="VEHICLEMOVEMENT").first()
    if dt is None:
        dt = DocumentType(code="VEHICLEMOVEMENT", name="Vehicle Movement",
                          requires_approval=True)
        db.session.add(dt)
    else:
        dt.requires_approval = True
    db.session.commit()
    mv = VehicleMovementService().create(
        vehicle_id=v.id, movement_type="TRANSFER",
        from_location="Carmona", to_location="Manila",
        movement_date=date.today(), user=u)
    db.session.commit()

    with pytest.raises(InvalidMovementStateError):
        VehicleMovementService().complete(mv.id)


def test_vehicle_movement_completes_when_approval_is_not_configured(app, env):
    """The flow an earlier version of this guard broke: a movement
    closed out after the fact, without Start Transit, on a document type
    that needs no approval. Requiring IN_TRANSIT there was a workflow
    opinion, not a safety gain."""
    from app.modules.transactions.vehicle_movement.service import (
        VehicleMovementService)
    from app.modules.document_config.models import DocumentType
    from datetime import date
    v, u = env
    dt = DocumentType.query.filter_by(code="VEHICLEMOVEMENT").first()
    if dt is None:
        dt = DocumentType(code="VEHICLEMOVEMENT", name="Vehicle Movement",
                          requires_approval=False)
        db.session.add(dt)
    else:
        dt.requires_approval = False
    db.session.commit()
    mv = VehicleMovementService().create(
        vehicle_id=v.id, movement_type="TRANSFER",
        from_location="Carmona", to_location="Manila",
        movement_date=date.today(), user=u)
    db.session.commit()

    VehicleMovementService().complete(mv.id)
    assert mv.status == "COMPLETED"
