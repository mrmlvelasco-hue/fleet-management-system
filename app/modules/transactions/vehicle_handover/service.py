"""Vehicle Handover Checklist service.

See models.py for the independence rule this module lives under. Reuses
only cross-cutting infrastructure (numbering, attachments, org/assignee
scope) -- nothing from the periodic-inspection checklist module.
"""
from datetime import date

from app.core.numbering.numbering_service import AutoNumberingService
from app.extensions import db
from app.modules.document_config.models import DocumentType, NumberingScheme
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.transactions.vehicle_handover.models import (
    DAMAGE_KINDS, DEFAULT_ITEMS, MARK_VALUES, HandoverDamageMark,
    HandoverItem, VehicleHandoverChecklist)

DOCUMENT_TYPE_CODE = "VHC"

#: Section II / IV are short, fixed lists on the paper form -- see the
#: models.py note on why these are JSON rather than child tables.
DEFAULT_DOCUMENTS = [
    "Official Receipt (OR)", "Registration Certificate (CR)",
    "LTO Validating Stickers", "Insurance Comprehensive & CTPL",
    "Vehicle Warranty", "Aircon Warranty", "Stereo Warranty",
    "Battery Warranty",
]
DEFAULT_OTHER_ITEMS = [
    "Painting", "Battery", "Fan Belts", "Engine", "Brake/Clutch Fluids",
    "Radiator", "Coolant Overflow Reservoir", "Windshield Washer Reservoir",
    "Steering Wheel", "Door Locks", "Trunk Compartment",
    "Lawanit Board - Trunk Compartment", "Mat - Trunk Compartment",
    "Gauges", "Brakes", "Hand Brake",
]


def _as_bool_list(labels):
    return [{"label": label, "present": False} for label in labels]


class VehicleHandoverService:

    # ── numbering ────────────────────────────────────────────────────

    def _ensure_document_type(self):
        """Idempotent get-or-create for this module's own DocumentType.

        Self-registering rather than relying on a CLI seed step someone
        has to remember to run -- the same reasoning PermissionRegistry
        already applies to permission codes, applied here to numbering
        so `create()` works the first time this module is ever called.
        """
        dt = DocumentType.query.filter_by(code=DOCUMENT_TYPE_CODE).first()
        if dt is None:
            dt = DocumentType(
                code=DOCUMENT_TYPE_CODE, name="Vehicle Handover Checklist",
                requires_approval=False, auto_numbering=True,
                printable=True, mobile_available=False,
                attachment_allowed=True,
                description="Vehicle issuance/return checklist, standalone "
                            "from the periodic inspection checklist module.")
            db.session.add(dt)
            db.session.flush()
        if dt.numbering_scheme is None:
            db.session.add(NumberingScheme(
                document_type_id=dt.id, prefix=DOCUMENT_TYPE_CODE,
                include_year=True, include_month=False, digit_count=6,
                separator="-", reset_policy="YEARLY"))
            db.session.flush()
        return dt

    # ── create ───────────────────────────────────────────────────────

    def create(self, vehicle_id, assignee_driver_id=None, actor=None):
        vehicle = Vehicle.query.get(vehicle_id)
        if vehicle is None:
            raise ValueError(f"No vehicle with id {vehicle_id}.")

        self._ensure_document_type()
        number = AutoNumberingService().generate(DOCUMENT_TYPE_CODE)

        assignee_name = position = None
        driver = None
        if assignee_driver_id:
            driver = Driver.query.get(assignee_driver_id)
            if driver is not None:
                # SNAPSHOT at creation time -- see the model docstring
                # and test_assignee_is_snapshotted_not_just_linked.
                assignee_name = f"{driver.first_name} {driver.last_name}".strip()
                position = getattr(driver, "position", None)

        doc = VehicleHandoverChecklist(
            document_number=number,
            vehicle_id=vehicle.id,
            branch_id=vehicle.branch_id,
            assignee_driver_id=driver.id if driver else None,
            assignee_name=assignee_name,
            position=position,
            status="DRAFT",
            documents_json=_as_bool_list(DEFAULT_DOCUMENTS),
            other_items_json=_as_bool_list(DEFAULT_OTHER_ITEMS),
            created_by=getattr(actor, "id", None),
        )
        db.session.add(doc)
        db.session.flush()

        for i, name in enumerate(DEFAULT_ITEMS):
            db.session.add(HandoverItem(
                handover_id=doc.id, item_name=name, sort_order=i,
                created_by=getattr(actor, "id", None)))
        db.session.commit()
        return doc

    # ── lifecycle ────────────────────────────────────────────────────

    def _get_or_404(self, handover_id):
        doc = VehicleHandoverChecklist.query.get(handover_id)
        if doc is None:
            raise ValueError(f"No handover checklist with id {handover_id}.")
        return doc

    def issue(self, handover_id, odometer=None, fuel_level=None,
             issued_by_name=None, received_by_name=None, actor=None):
        doc = self._get_or_404(handover_id)
        if doc.status != "DRAFT":
            raise ValueError(
                f"Cannot issue a checklist in status {doc.status!r}; "
                "it must be DRAFT.")
        doc.status = "ISSUED"
        doc.date_issued = date.today()
        doc.odometer_at_issuance = odometer
        doc.fuel_level = fuel_level
        doc.issued_by_name = issued_by_name
        doc.issued_by_date = date.today() if issued_by_name else None
        doc.issuance_received_by_name = received_by_name
        doc.issuance_received_by_date = date.today() if received_by_name else None
        doc.updated_by = getattr(actor, "id", None)
        db.session.commit()
        return doc

    def return_unit(self, handover_id, odometer=None,
                    returned_by_name=None, received_by_name=None,
                    actor=None):
        doc = self._get_or_404(handover_id)
        if doc.status != "ISSUED":
            # The paper form's own ordering: nothing to compare the
            # Return column against until the Issuance column exists.
            raise ValueError(
                "Cannot return a checklist that has not been issued "
                f"(current status {doc.status!r}).")
        doc.status = "RETURNED"
        doc.date_returned = date.today()
        doc.odometer_at_return = odometer
        doc.returned_by_name = returned_by_name
        doc.returned_by_date = date.today() if returned_by_name else None
        doc.return_received_by_name = received_by_name
        doc.return_received_by_date = date.today() if received_by_name else None
        doc.updated_by = getattr(actor, "id", None)
        db.session.commit()
        return doc

    # ── items & damage ───────────────────────────────────────────────

    def mark_item(self, item_id, phase, mark=None, qty=None, actor=None):
        if phase not in ("issuance", "return"):
            raise ValueError(f"phase must be 'issuance' or 'return', got {phase!r}.")
        if mark is not None and mark not in MARK_VALUES:
            raise ValueError(f"mark must be one of {MARK_VALUES}, got {mark!r}.")
        item = HandoverItem.query.get(item_id)
        if item is None:
            raise ValueError(f"No handover item with id {item_id}.")
        setattr(item, f"{phase}_mark", mark)
        setattr(item, f"{phase}_qty", qty)
        item.updated_by = getattr(actor, "id", None)
        db.session.commit()
        return item

    def add_damage(self, handover_id, kind, x, y, note=None, actor=None):
        if kind not in DAMAGE_KINDS:
            raise ValueError(f"kind must be one of {DAMAGE_KINDS}, got {kind!r}.")
        doc = self._get_or_404(handover_id)
        mark = HandoverDamageMark(
            handover_id=doc.id, kind=kind, x=x, y=y, note=note,
            created_by=getattr(actor, "id", None))
        db.session.add(mark)
        db.session.commit()
        return mark

    def remove_damage(self, mark_id, actor=None):
        mark = HandoverDamageMark.query.get(mark_id)
        if mark is not None:
            db.session.delete(mark)
            db.session.commit()

    # ── visibility ───────────────────────────────────────────────────

    def list_visible(self, user):
        """Same narrowing rule as the vehicle list (flask-v251).

        An issuance/return record is exactly the kind of document a
        restricted assignee should see for their own vehicle and no
        other -- so this reuses AssigneeScopeService and
        UserOrgScopeService rather than inventing a third rule.
        """
        query = VehicleHandoverChecklist.query
        if getattr(user, "restrict_to_assigned_vehicles", False):
            from app.modules.user_management.assignee_scope_service import (
                AssigneeScopeService)
            ids = AssigneeScopeService().assigned_vehicle_ids(user)
            return (query.filter(
                VehicleHandoverChecklist.vehicle_id.in_(ids)).all()
                if ids else [])

        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        scopes = scope_svc.list_for_user(getattr(user, "id", None))
        if not scopes:
            return query.all()
        unrestricted = any(sc.scope_type in ("GLOBAL", "COMPANY")
                           for sc in scopes)
        if unrestricted:
            return query.all()
        branch_ids = [sc.branch_id for sc in scopes
                     if sc.scope_type == "BRANCH" and sc.branch_id]
        return query.filter(
            VehicleHandoverChecklist.branch_id.in_(branch_ids)).all() \
            if branch_ids else []

    def get_visible(self, handover_id, user):
        doc = VehicleHandoverChecklist.query.get(handover_id)
        if doc is None:
            return None
        if getattr(user, "restrict_to_assigned_vehicles", False):
            from app.modules.user_management.assignee_scope_service import (
                AssigneeScopeService)
            if not AssigneeScopeService().covers_vehicle(user, doc.vehicle_id):
                return None
            return doc
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        if UserOrgScopeService().covers(getattr(user, "id", None),
                                        branch_id=doc.branch_id):
            return doc
        return None

    # ── report payload ───────────────────────────────────────────────

    def to_report(self, handover_id):
        """Shape this EXACTLY like react-v191's VehicleChecklistReport
        props. No reshaping belongs in the route handler -- that is
        where a field would get silently dropped on the way through.
        """
        doc = self._get_or_404(handover_id)
        v = doc.vehicle

        identification = [
            {"label": "Plate No. / CS No.", "value": v.plate_number},
            {"label": "Make / Model / Year",
             "value": f"{v.brand} {v.model} {v.year}".strip()},
            {"label": "Color", "value": getattr(v, "color", None)},
            {"label": "Odometer Reading",
             "value": (f"{doc.odometer_at_issuance:,} km"
                      if doc.odometer_at_issuance is not None else None)},
            {"label": "Engine No.", "value": getattr(v, "engine_number", None)},
            {"label": "Chassis No.", "value": getattr(v, "chassis_number", None)},
        ]

        third = (len(doc.items) + 2) // 3
        cols = [doc.items[i:i + third] for i in range(0, len(doc.items), third)]
        while len(cols) < 3:
            cols.append([])

        def col(items):
            return {"lines": [{
                "label": i.item_name,
                "issuance": i.issuance_mark, "issuance_qty": i.issuance_qty,
                "return_": i.return_mark, "return_qty": i.return_qty,
            } for i in items]}

        return {
            "assignee_name": doc.assignee_name,
            "position": doc.position,
            "company_bu": doc.company_bu,
            "date_issued": doc.date_issued.isoformat() if doc.date_issued else None,
            "date_returned": doc.date_returned.isoformat() if doc.date_returned else None,
            "identification": identification,
            "documents": doc.documents_json or [],
            "columns": [col(cols[0]), col(cols[1]), col(cols[2])],
            "other_items": doc.other_items_json or [],
            "fuel_level": doc.fuel_level,
            "damages": [{"kind": d.kind, "x": d.x, "y": d.y, "note": d.note}
                       for d in doc.damages],
            "remarks": doc.remarks,
            "signatures": [
                {"label": "Issued by", "name": doc.issued_by_name,
                 "date": doc.issued_by_date.isoformat() if doc.issued_by_date else None},
                {"label": "Received by", "name": doc.issuance_received_by_name,
                 "date": doc.issuance_received_by_date.isoformat()
                 if doc.issuance_received_by_date else None},
                {"label": "Returned by", "name": doc.returned_by_name,
                 "date": doc.returned_by_date.isoformat() if doc.returned_by_date else None},
                {"label": "Received by", "name": doc.return_received_by_name,
                 "date": doc.return_received_by_date.isoformat()
                 if doc.return_received_by_date else None},
            ],
        }
