"""Document edit policy, Phase 2: every request document.

Spec: docs/superpowers/specs/2026-10-12-document-edit-policy-design.md
(section 4).

The Maintenance Order rule from Phase 1, lifted into
BaseTransactionService and applied to Purchase Request, Trip Ticket,
Vehicle Registration, Vehicle Movement, Tire and Battery transactions
and Authority To Drive:

* open while DRAFT -- unsubmitted, Pending, Returned or Approved but not
  yet actioned -- and not Rejected; locked once the document leaves
  Draft (released / active / in transit / ordered / completed /
  cancelled);
* the initiator or a <doc>.update holder may edit, nobody else;
* approval is not restarted: approvers get edited_after_submission and
  the detail lists changes_since_submission.

Before this, only ATD could be edited at all (Draft/Returned only), a
returned PR could not be corrected, and the other five had no edit.
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.cli import _seed_notification_rules
from app.core.approval.models import ApprovalAction, ApprovalInstance
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.approval_config.models import ApprovalLevel, ApprovalPath
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import DocumentTypeService
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.service import BranchService, DepartmentService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.system_admin.models import InAppNotification
from app.modules.transactions.atd.service import ATDService
from app.modules.transactions.base_service import (
    DocumentEditForbiddenError, DocumentFieldError, DocumentStateError)
from app.modules.transactions.battery_txn.service import (
    BatteryTransactionService)
from app.modules.transactions.purchase_request.service import (
    PurchaseRequestService)
from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.transactions.trip_ticket.service import TripTicketService
from app.modules.transactions.vehicle_movement.service import (
    VehicleMovementService)
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)
from app.modules.user_management.models import Permission, Role, User
from tests.conftest import tiny_jpeg_file

MODULES = ["purchaserequest", "tripticket", "vehicleregistration",
           "vehiclemovement", "tire", "battery", "atd"]


def _role(db, name, actions):
    codes = [f"{m}.{a}" for m in MODULES for a in actions]
    r = Role(name=name, permissions=Permission.query.filter(
        Permission.code.in_(codes)).all())
    db.session.add(r)
    db.session.commit()
    return r


def _user(db, username, role):
    u = User(username=username, email=f"{username}@e.com",
             first_name=username.split("_")[-1].title(), last_name="Test",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles = [role]
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def env(db):
    sync_permissions()
    for code in ("PR", "TT", "VR", "VM", "TIR", "BAT", "ATD"):
        if DocumentType.query.filter_by(code=code).first() is None:
            DocumentTypeService().create(code=code, name=code,
                                         requires_approval=True,
                                         auto_numbering=False)
    branch = BranchService().create(code="BR-DE", name="Manila Hub")
    dept = DepartmentService().create(code="OPS", name="Operations",
                                      branch_id=branch.id)
    vt = VehicleTypeService().create(code="LV-DE", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2012,
        branch_id=branch.id, conduction_number="TIL-120")
    driver = DriverService().create(
        employee_number="EMP-DE1", first_name="Maria", last_name="Santos",
        license_number="LIC-DE1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id,
        photo_file=tiny_jpeg_file())
    tire = TireService().create(serial_number="T-DE1", brand="Bridgestone",
                                size="185/65R15", tire_type="RADIAL")
    battery = BatteryService().create(serial_number="B-DE1", brand="Motolite")

    view = _role(db, "DE viewers", ["view"])
    edit = _role(db, "DE editors", ["view", "update"])
    appr = _role(db, "DE approvers", ["view"])
    users = {"initiator": _user(db, "de_initiator", view),
             "editor": _user(db, "de_editor", edit),
             "stranger": _user(db, "de_stranger", view),
             "approver": _user(db, "de_approver", appr)}
    path = ApprovalPath(name="DE One-Step")
    db.session.add(path)
    db.session.flush()
    db.session.add(ApprovalLevel(path_id=path.id, level_number=1,
                                 approver_type="ROLE", role_id=appr.id))
    db.session.commit()
    _seed_notification_rules()
    db.session.commit()
    return dict(vehicle=vehicle, driver=driver, tire=tire, battery=battery,
                dept=dept, path=path, **users)


# ── one entry per document ──────────────────────────────────────────
# make(env, user) -> record; edit = (field, new value, label, shown as);
# locked = a physical status past Draft; api = REST collection.

DOCS = {
    "purchase_request": dict(
        svc=PurchaseRequestService, api="purchase-requests",
        make=lambda e, u: PurchaseRequestService().create(
            description="Brake pads", user=u, department_id=e["dept"].id,
            lines=[{"item_description": "Pad", "quantity": 2,
                    "unit_cost": 500}]),
        edit=("description", "Brake pads and rotors", "Description",
              "Brake pads and rotors"),
        locked="RECEIVED", ongoing="ORDERED"),
    "trip_ticket": dict(
        svc=TripTicketService, api="trip-tickets",
        make=lambda e, u: TripTicketService().create(
            vehicle_id=e["vehicle"].id, destination="Muntinlupa",
            purpose="Delivery", departure_datetime=datetime(2026, 10, 14, 8),
            odometer_out=1000, user=u, driver_id=e["driver"].id),
        edit=("destination", "Cebu", "Destination", "Cebu"),
        locked="COMPLETED", ongoing="RELEASED"),
    "vehicle_registration": dict(
        svc=VehicleRegistrationService, api="vehicle-registrations",
        make=lambda e, u: VehicleRegistrationService().create(
            vehicle_id=e["vehicle"].id, registration_type="NEW",
            registration_date=date(2026, 10, 1), user=u),
        edit=("or_cr_cost", 1500, "OR/CR cost", "1,500.00"),
        locked="COMPLETED"),
    "vehicle_movement": dict(
        svc=VehicleMovementService, api="vehicle-movements",
        make=lambda e, u: VehicleMovementService().create(
            vehicle_id=e["vehicle"].id, movement_type="TRANSFER",
            from_location="Manila Hub", to_location="Muntinlupa",
            movement_date=date(2026, 10, 14), user=u),
        edit=("to_location", "Cebu Branch", "To", "Cebu Branch"),
        locked="COMPLETED", ongoing="IN_TRANSIT"),
    "tire_transaction": dict(
        svc=TireTransactionService, api="tire-transactions",
        make=lambda e, u: TireTransactionService().create(
            tire_id=e["tire"].id, action="MOUNT",
            transaction_date=date(2026, 10, 14), user=u,
            vehicle_id=e["vehicle"].id),
        edit=("remarks", "Front left", "Remarks", "Front left"),
        locked="COMPLETED"),
    "battery_transaction": dict(
        svc=BatteryTransactionService, api="battery-transactions",
        make=lambda e, u: BatteryTransactionService().create(
            battery_id=e["battery"].id, action="MOUNT",
            transaction_date=date(2026, 10, 14), user=u,
            vehicle_id=e["vehicle"].id),
        edit=("remarks", "Replaced", "Remarks", "Replaced"),
        locked="COMPLETED"),
    "atd": dict(
        svc=ATDService, api="atd",
        make=lambda e, u: ATDService().create(
            vehicle_id=e["vehicle"].id, driver_id=e["driver"].id,
            purpose="Field visit", valid_from=date(2026, 10, 14),
            valid_to=date(2026, 10, 20), user=u),
        edit=("purpose", "Client visit", "Purpose", "Client visit"),
        locked="CANCELLED", ongoing="ACTIVE"),
}
NAMES = list(DOCS)


def _submit(db, env, record, svc, status="PENDING", approved_by=None):
    dt = DocumentType.query.filter_by(code=svc.document_type_code).first()
    inst = ApprovalInstance(
        document_type_id=dt.id, approval_path_id=env["path"].id,
        reference_table=svc.reference_table, reference_id=record.id,
        status=status, current_level=1,
        submitted_by=env["initiator"].id)
    db.session.add(inst)
    db.session.flush()
    db.session.add(ApprovalAction(
        instance_id=inst.id, level_number=1, action="SUBMIT",
        acted_by=env["initiator"].id,
        # Now, not back-dated: the record's own creation-time writes (a
        # PR total computed from its lines) happen before submission.
        acted_at=datetime.now(timezone.utc)))
    if approved_by is not None:
        db.session.add(ApprovalAction(
            instance_id=inst.id, level_number=1, action="APPROVE",
            acted_by=approved_by.id, acted_at=datetime.now(timezone.utc)))
    record.approval_instance_id = inst.id
    db.session.commit()
    InAppNotification.query.delete()
    db.session.commit()
    return inst


def _new(env, name):
    return DOCS[name]["make"](env, env["initiator"])


def _hdr(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# ── the shared rule, on every document ──────────────────────────────

@pytest.mark.parametrize("name", NAMES)
def test_initiator_can_edit_while_pending(db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    _submit(db, env, rec, d["svc"])
    field, value = d["edit"][0], d["edit"][1]
    d["svc"]().update(rec.id, user=env["initiator"], **{field: value})
    db.session.expire_all()
    got = getattr(db.session.get(d["svc"].model, rec.id), field)
    assert str(got).rstrip("0").rstrip(".") == str(value)


@pytest.mark.parametrize("name", NAMES)
def test_update_permission_holder_can_edit(db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    assert d["svc"]().can_edit(rec, env["editor"])


@pytest.mark.parametrize("name", NAMES)
def test_other_user_cannot_edit(db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    assert not d["svc"]().can_edit(rec, env["stranger"])
    with pytest.raises(DocumentEditForbiddenError):
        d["svc"]().update(rec.id, user=env["stranger"],
                          **{d["edit"][0]: d["edit"][1]})


ONGOING = [n for n in NAMES if DOCS[n].get("ongoing")]


@pytest.mark.parametrize("name", ONGOING)
def test_ongoing_document_is_editable_like_an_in_progress_mo(db, env, name):
    """Same standard process as the Maintenance Order (Michael,
    2026-10-13): after approval, while the work is ongoing -- PR ordered,
    trip released, ATD active, movement in transit -- the document can
    still be corrected, and the approvers who signed off are told."""
    d = DOCS[name]
    rec = _new(env, name)
    _submit(db, env, rec, d["svc"], status="APPROVED",
            approved_by=env["approver"])
    rec.status = d["ongoing"]
    db.session.commit()
    svc = d["svc"]()
    assert svc.editable_scope(rec) == "FULL"
    field, value = d["edit"][0], d["edit"][1]
    svc.update(rec.id, user=env["initiator"], **{field: value})
    assert InAppNotification.query.filter_by(
        user_id=env["approver"].id,
        event_code="edited_after_submission").count() == 1


@pytest.mark.parametrize("name", NAMES)
def test_locked_once_closed(db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    rec.status = d["locked"]
    db.session.commit()
    svc = d["svc"]()
    assert svc.editable_scope(rec) == "NONE"
    assert not svc.can_edit(rec, env["initiator"])
    with pytest.raises(DocumentStateError):
        svc.update(rec.id, user=env["editor"], **{d["edit"][0]: d["edit"][1]})


@pytest.mark.parametrize("name", NAMES)
def test_rejected_is_locked(db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    _submit(db, env, rec, d["svc"], status="REJECTED")
    assert d["svc"]().editable_scope(rec) == "NONE"


@pytest.mark.parametrize("name", NAMES)
def test_pending_edit_notifies_the_approver_and_is_listed(db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    _submit(db, env, rec, d["svc"])
    field, value, label, shown = d["edit"]
    svc = d["svc"]()
    svc.update(rec.id, user=env["initiator"], **{field: value})
    assert InAppNotification.query.filter_by(
        user_id=env["approver"].id,
        event_code="edited_after_submission").count() == 1
    feed = svc.changes_since_submission(db.session.get(d["svc"].model, rec.id))
    assert [(c["label"], c["new"]) for c in feed] == [(label, shown)]


@pytest.mark.parametrize("name", NAMES)
def test_unsubmitted_edit_notifies_nobody(db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    d["svc"]().update(rec.id, user=env["initiator"],
                      **{d["edit"][0]: d["edit"][1]})
    assert InAppNotification.query.filter_by(
        event_code="edited_after_submission").count() == 0


@pytest.mark.parametrize("name", NAMES)
def test_fields_outside_the_edit_list_are_ignored(db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    before = rec.status
    d["svc"]().update(rec.id, user=env["initiator"], status="COMPLETED",
                      requested_by=env["stranger"].id)
    rec = db.session.get(d["svc"].model, rec.id)
    assert rec.status == before
    assert rec.requested_by == env["initiator"].id


# ── API ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", NAMES)
def test_api_initiator_edits_while_pending(client, db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    _submit(db, env, rec, d["svc"])
    field, value = d["edit"][0], d["edit"][1]
    r = client.patch(f"/api/v1/{d['api']}/{rec.id}", json={field: value},
                     headers=_hdr(client, "de_initiator"))
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["can_edit"] is True
    assert body["changes_since_submission"][0]["label"] == d["edit"][2]


@pytest.mark.parametrize("name", NAMES)
def test_api_stranger_is_refused(client, db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    r = client.patch(f"/api/v1/{d['api']}/{rec.id}",
                     json={d["edit"][0]: d["edit"][1]},
                     headers=_hdr(client, "de_stranger"))
    assert r.status_code == 403


@pytest.mark.parametrize("name", NAMES)
def test_api_closed_document_is_a_conflict(client, db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    rec.status = d["locked"]
    db.session.commit()
    r = client.patch(f"/api/v1/{d['api']}/{rec.id}",
                     json={d["edit"][0]: d["edit"][1]},
                     headers=_hdr(client, "de_editor"))
    assert r.status_code == 409


@pytest.mark.parametrize("name", NAMES)
def test_api_detail_reports_can_edit_and_the_feed(client, db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    mine = client.get(f"/api/v1/{d['api']}/{rec.id}",
                      headers=_hdr(client, "de_initiator")).get_json()
    other = client.get(f"/api/v1/{d['api']}/{rec.id}",
                       headers=_hdr(client, "de_stranger")).get_json()
    assert mine["can_edit"] is True and other["can_edit"] is False
    assert mine["changes_since_submission"] == []


# ── each document's own rules ───────────────────────────────────────

def test_trip_ticket_still_requires_a_master_driver(db, env):
    rec = _new(env, "trip_ticket")
    with pytest.raises(DocumentFieldError) as exc:
        TripTicketService().update(rec.id, user=env["initiator"],
                                   driver_id=None)
    assert exc.value.field == "driver_id"


def test_vehicle_movement_type_must_be_valid(db, env):
    rec = _new(env, "vehicle_movement")
    with pytest.raises(DocumentFieldError) as exc:
        VehicleMovementService().update(rec.id, user=env["initiator"],
                                        movement_type="TELEPORT")
    assert exc.value.field == "movement_type"


@pytest.mark.parametrize("name", ["tire_transaction", "battery_transaction"])
def test_asset_action_must_be_valid(db, env, name):
    rec = _new(env, name)
    with pytest.raises(DocumentFieldError) as exc:
        DOCS[name]["svc"]().update(rec.id, user=env["initiator"],
                                   action="JUGGLE")
    assert exc.value.field == "action"


def test_registration_expiry_follows_date_and_validity(db, env):
    rec = _new(env, "vehicle_registration")
    VehicleRegistrationService().update(
        rec.id, user=env["initiator"], registration_date=date(2026, 11, 1),
        validity_years=1)
    rec = db.session.get(VehicleRegistrationService.model, rec.id)
    assert rec.expiry_date == date(2027, 11, 1)


def _second_vehicle(env):
    from app.modules.master_data.vehicle.models import Vehicle
    v = env["vehicle"]
    return VehicleService().create(
        vehicle_type_id=v.vehicle_type_id, brand="Isuzu", model="D-Max",
        year=2019, branch_id=v.branch_id, conduction_number="NAB-4471")


def test_registration_vehicle_can_be_corrected(db, env):
    """Like the MO's vehicle: editable, with the creation rules re-run."""
    rec = _new(env, "vehicle_registration")
    other = _second_vehicle(env)
    VehicleRegistrationService().update(rec.id, user=env["initiator"],
                                        vehicle_id=other.id)
    assert db.session.get(VehicleRegistrationService.model,
                          rec.id).vehicle_id == other.id


def test_registration_vehicle_change_rechecks_duplicates(db, env):
    other = _second_vehicle(env)
    VehicleRegistrationService().create(
        vehicle_id=other.id, registration_type="NEW",
        registration_date=date(2026, 10, 1), user=env["initiator"])
    rec = _new(env, "vehicle_registration")
    with pytest.raises(DocumentFieldError) as exc:
        VehicleRegistrationService().update(rec.id, user=env["initiator"],
                                            vehicle_id=other.id)
    assert exc.value.field == "vehicle_id"


def test_registration_resave_is_not_a_duplicate_of_itself(db, env):
    rec = _new(env, "vehicle_registration")
    VehicleRegistrationService().update(
        rec.id, user=env["initiator"], vehicle_id=env["vehicle"].id,
        registration_type="NEW", or_cr_cost=100)
    assert float(db.session.get(VehicleRegistrationService.model,
                                rec.id).or_cr_cost) == 100


def test_registration_type_change_rechecks_prior_registration(db, env):
    """RENEWAL needs a prior registration, exactly as at creation."""
    rec = _new(env, "vehicle_registration")
    with pytest.raises(DocumentFieldError) as exc:
        VehicleRegistrationService().update(rec.id, user=env["initiator"],
                                            registration_type="RENEWAL")
    assert exc.value.field == "registration_type"


def test_purchase_request_lines_editable_while_pending(db, env):
    rec = _new(env, "purchase_request")
    _submit(db, env, rec, PurchaseRequestService)
    PurchaseRequestService().add_line(rec.id, item_description="Rotor",
                                      quantity=1, unit_cost=2000)
    assert len(db.session.get(PurchaseRequestService.model, rec.id).lines) == 2


def test_pending_pr_line_change_notifies_and_lists_the_new_total(db, env):
    """A line added while Pending changes the PR total -- the figure the
    approval band is based on -- so the approver must hear about it and
    see it, like any other edit."""
    rec = _new(env, "purchase_request")            # 2 x 500 = 1,000.00
    _submit(db, env, rec, PurchaseRequestService)
    svc = PurchaseRequestService()
    svc.add_line(rec.id, item_description="Rotor", quantity=1,
                 unit_cost=2000)
    assert InAppNotification.query.filter_by(
        user_id=env["approver"].id,
        event_code="edited_after_submission").count() == 1
    feed = svc.changes_since_submission(
        db.session.get(PurchaseRequestService.model, rec.id))
    assert [(c["label"], c["old"], c["new"]) for c in feed] == [
        ("Total amount", "1,000.00", "3,000.00")]


def test_pr_amount_is_listed_but_not_editable_directly(db, env):
    rec = _new(env, "purchase_request")
    PurchaseRequestService().update(rec.id, user=env["initiator"],
                                    amount=1)
    assert float(db.session.get(PurchaseRequestService.model,
                                rec.id).amount) == 1000.0


def test_purchase_request_lines_editable_once_ordered(db, env):
    """Ordered = work ongoing, open like an In-Progress MO."""
    rec = _new(env, "purchase_request")
    rec.status = "ORDERED"
    db.session.commit()
    PurchaseRequestService().add_line(rec.id, item_description="Rotor",
                                      quantity=1, unit_cost=2000)
    assert len(db.session.get(PurchaseRequestService.model, rec.id).lines) == 2


def test_purchase_request_lines_locked_once_received(db, env):
    from app.modules.transactions.purchase_request.service import (
        LineManagementError)
    rec = _new(env, "purchase_request")
    rec.status = "RECEIVED"
    db.session.commit()
    with pytest.raises(LineManagementError):
        PurchaseRequestService().add_line(rec.id, item_description="Rotor",
                                          quantity=1, unit_cost=2000)


def test_api_initiator_can_add_a_pr_line(client, db, env):
    rec = _new(env, "purchase_request")
    _submit(db, env, rec, PurchaseRequestService)
    r = client.post(f"/api/v1/purchase-requests/{rec.id}/lines",
                    json={"item_description": "Rotor", "quantity": 1,
                          "unit_cost": 2000},
                    headers=_hdr(client, "de_initiator"))
    assert r.status_code in (200, 201), r.get_data(as_text=True)


def test_api_stranger_cannot_add_a_pr_line(client, db, env):
    rec = _new(env, "purchase_request")
    r = client.post(f"/api/v1/purchase-requests/{rec.id}/lines",
                    json={"item_description": "Rotor", "quantity": 1,
                          "unit_cost": 2000},
                    headers=_hdr(client, "de_stranger"))
    assert r.status_code == 403


# ── Tire / Battery: Return and Resubmit (absent before) ─────────────

@pytest.mark.parametrize("name", ["tire_transaction", "battery_transaction"])
def test_asset_transaction_can_be_returned_and_resubmitted(
        client, db, env, name):
    d = DOCS[name]
    rec = _new(env, name)
    _submit(db, env, rec, d["svc"])
    r = client.post(f"/api/v1/{d['api']}/{rec.id}/return",
                    json={"remarks": "Wrong tire"},
                    headers=_hdr(client, "de_approver"))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["approval_instance_status"] == "RETURNED"
    # Resubmit keeps the existing <asset>.update gate, like Submit and
    # like every other document's resubmit.
    r = client.post(f"/api/v1/{d['api']}/{rec.id}/resubmit", json={},
                    headers=_hdr(client, "de_editor"))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["approval_instance_status"] == "PENDING"


def test_api_pr_locked_flag_follows_the_edit_policy(client, db, env):
    """The detail's `locked` flag is the same decision the service makes:
    open while Pending/Ordered, locked once Received."""
    rec = _new(env, "purchase_request")
    _submit(db, env, rec, PurchaseRequestService)
    hdr = _hdr(client, "de_initiator")
    url = f"/api/v1/purchase-requests/{rec.id}"
    assert client.get(url, headers=hdr).get_json()["locked"] is False
    rec = db.session.get(PurchaseRequestService.model, rec.id)
    rec.status = "ORDERED"
    db.session.commit()
    assert client.get(url, headers=hdr).get_json()["locked"] is False
    rec.status = "RECEIVED"
    db.session.commit()
    assert client.get(url, headers=hdr).get_json()["locked"] is True
