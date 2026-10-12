"""Maintenance Order service: creation (with checklist generation from a
PM Scope Template), the shared submit/approve/reject/return/cancel
lifecycle, plus start_work/complete physical-lifecycle actions.

Preventive orders require every checklist item completed before the order
can be marked COMPLETED; Corrective orders have no checklist requirement
(unscheduled/reactive repair work)."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.maintenance_order.models import (
    MaintenanceOrder, MaintenanceChecklistItem, TransactionType)
from app.modules.master_data.reference.models import MaintenanceType
from app.modules.maintenance_config.models import PMScopeTemplate


class IncompleteChecklistError(Exception):
    pass


class InvalidOrderStateError(Exception):
    pass


class InvalidOrderCategoryError(Exception):
    pass


class OrderFieldError(InvalidOrderCategoryError):
    """A field value the order cannot take (destination = origin, a
    transaction type from another group). Carries the field name so the
    API can return a field-named 400 the form can show beside the
    control. Subclasses InvalidOrderCategoryError so existing callers
    that catch that keep working."""

    def __init__(self, message, field="_"):
        super().__init__(message)
        self.field = field


class OrderEditForbiddenError(Exception):
    """The user may not edit this order: they are neither its initiator
    nor a holder of maintenanceorder.update. Deliberately NOT an
    InvalidOrderStateError -- the order is fine, the caller is not
    allowed -- so the API can answer 403 rather than 409."""
    pass


def _naive_utc(dt):
    """Audit and approval timestamps are written timezone-aware (UTC)
    but SQLite hands them back naive; compare them all as naive UTC."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _change_display(field, value):
    """An audit value as a person would read it: names instead of ids,
    money with separators. Never raises -- a record deleted since the
    change still shows its id rather than breaking the list."""
    if value in (None, ""):
        return "—"
    try:
        if field in ("estimated_cost", "disposal_value"):
            return f"{Decimal(str(value)):,.2f}"
        lookup = {
            "vehicle_id": ("app.modules.master_data.vehicle.models",
                           "Vehicle",
                           lambda r: r.plate_number or r.conduction_number),
            "transaction_type_id": (
                "app.modules.transactions.maintenance_order.models",
                "TransactionType", lambda r: r.name),
            "maintenance_type_id": (
                "app.modules.master_data.reference.models",
                "MaintenanceType", lambda r: r.name),
            "scope_template_id": ("app.modules.maintenance_config.models",
                                  "PMScopeTemplate", lambda r: r.name),
            "vendor_id": ("app.modules.master_data.vendor.models", "Vendor",
                          lambda r: r.name),
            "driver_id": ("app.modules.master_data.driver.models", "Driver",
                          lambda r: f"{r.first_name} {r.last_name}".strip()),
            "destination_branch_id": ("app.modules.master_data.org.models",
                                      "Branch", lambda r: r.name),
        }.get(field)
        if lookup:
            import importlib
            module, cls, show = lookup
            model = getattr(importlib.import_module(module), cls)
            row = db.session.get(model, int(value))
            return show(row) if row else f"#{value}"
    except Exception:
        pass
    return str(value)


class ScopeValidationError(ValueError):
    """A Scope of Work line failed validation (blank description, or a
    field longer than its column). Raised BEFORE anything is written, so
    a bad line never leaves a half-saved order behind."""
    pass


# Column widths of maintenance_checklist_items -- checked here so the
# user gets a readable message rather than a database truncation error
# (MySQL strict mode) or silent truncation (non-strict).
SCOPE_CODE_MAX = 40
SCOPE_DESCRIPTION_MAX = 255


def normalize_scope_rows(rows) -> list:
    """Validate and clean incoming Scope of Work lines.

    Accepts a list of dicts with optional `id`, optional `activity_code`
    and required `activity_description`. Returns a list of
    {"id", "activity_code", "activity_description"} with whitespace
    trimmed, a blank code turned into None, and `id` coerced to int (or
    None for a new line). Order of the input list is the sort order.
    """
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        raise ScopeValidationError("Scope of Work must be a list of lines.")
    out = []
    for pos, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ScopeValidationError(
                f"Scope of Work line {pos} is not a valid entry.")
        desc = str(row.get("activity_description") or "").strip()
        code = str(row.get("activity_code") or "").strip() or None
        if not desc:
            raise ScopeValidationError(
                f"Scope of Work line {pos}: description is required.")
        if len(desc) > SCOPE_DESCRIPTION_MAX:
            raise ScopeValidationError(
                f"Scope of Work line {pos}: description must be at most "
                f"{SCOPE_DESCRIPTION_MAX} characters.")
        if code and len(code) > SCOPE_CODE_MAX:
            raise ScopeValidationError(
                f"Scope of Work line {pos}: code must be at most "
                f"{SCOPE_CODE_MAX} characters.")
        raw_id = row.get("id")
        try:
            row_id = int(raw_id) if raw_id not in (None, "") else None
        except (TypeError, ValueError):
            raise ScopeValidationError(
                f"Scope of Work line {pos} has an invalid id.")
        # Client hint only (audit/reporting aid): an untouched line the
        # form pre-filled from a template may say TEMPLATE; anything else
        # is MANUAL. create() further ignores TEMPLATE when the order has
        # no template at all.
        origin = "TEMPLATE" if row.get("origin") == "TEMPLATE" else "MANUAL"
        out.append({"id": row_id, "activity_code": code,
                    "activity_description": desc, "origin": origin})
    return out



PRINT_TEMPLATE_LABELS = {
    "vehicle_assignment_memo": "Vehicle Assignment Memo",
    "vehicle_reassignment_memo": "Vehicle Reassignment Memo",
    "vehicle_relocation_memo": "Vehicle Relocation Memo",
    "maintenanceorder_print_transfer": "Asset Transfer Report",
    "maintenanceorder_print_disposal": "Asset Disposal Report",
    "pm_work_order": "PM Work Order",
    "repair_work_order": "Repair Work Order",
    "maintenanceorder_print": "Work Order",
}

def resolve_print_template(tt):
    """Configured column first; otherwise infer from the type code/group
    so Assignment still prints as a VAM when print_template was never
    backfilled."""
    if tt is None:
        return "maintenanceorder_print"
    configured = getattr(tt, "print_template", None)
    if configured:
        return configured
    code = (getattr(tt, "code", None) or "").upper()
    group = (getattr(tt, "group", None) or "").upper()
    by_code = {
        "DEP-ASSIGNMENT": "vehicle_assignment_memo",
        "DEP-REASSIGNMENT": "vehicle_reassignment_memo",
        "DEP-RELOCATION": "vehicle_relocation_memo",
        "DEP-TRANSFER": "maintenanceorder_print_transfer",
        "MAINT-SERVICING": "pm_work_order",
        "MAINT-REPAIR": "repair_work_order",
    }
    if code in by_code:
        return by_code[code]
    if code.startswith("DIS-") or group == "DISPOSAL":
        return "maintenanceorder_print_disposal"
    if group == "DEPLOYMENT":
        return "vehicle_assignment_memo"
    return "maintenanceorder_print"

class TransactionTypeService:
    def create(self, *, code, name, order_category, group=None,
               sort_order=0, default_scope_template_id=None):
        tt = TransactionType(code=code, name=name, order_category=order_category,
                             group=group, sort_order=sort_order,
                             default_scope_template_id=default_scope_template_id)
        db.session.add(tt)
        db.session.commit()
        return tt

    def update(self, tt_id, *, name=None, order_category=None, group=None,
               sort_order=None, maintenance_class=None,
               default_scope_template_id="__unset__"):
        """Update editable fields on a TransactionType.

        default_scope_template_id uses a sentinel default (__unset__)
        so the caller can explicitly pass None (clear the template) vs
        not passing it at all (leave unchanged).
        """
        from app.modules.maintenance_config.models import PMScopeTemplate
        tt = self.get_by_id(tt_id)
        if tt is None:
            raise InvalidOrderStateError("Transaction type not found.")
        if name is not None:
            tt.name = name.strip()
        if order_category is not None:
            if order_category not in ("MAINTENANCE", "OPERATIONAL"):
                raise InvalidOrderCategoryError(
                    "order_category must be MAINTENANCE or OPERATIONAL.")
            tt.order_category = order_category
        if group is not None:
            tt.group = group or None
        if sort_order is not None:
            tt.sort_order = int(sort_order)
        if maintenance_class is not None:
            valid = {"PREVENTIVE", "CORRECTIVE", "PREDICTIVE", None, ""}
            mc = maintenance_class or None
            if mc and mc not in valid:
                raise InvalidOrderCategoryError(
                    "maintenance_class must be PREVENTIVE, CORRECTIVE, "
                    "PREDICTIVE, or empty.")
            tt.maintenance_class = mc or None
        if default_scope_template_id != "__unset__":
            if default_scope_template_id is not None:
                tmpl = db.session.get(PMScopeTemplate,
                                      default_scope_template_id)
                if tmpl is None or not tmpl.is_active:
                    raise InvalidOrderStateError(
                        "That PM Scope Template does not exist or is "
                        "inactive.")
                if (tt.order_category == "OPERATIONAL"
                        or (tt.order_category == "MAINTENANCE"
                            and tmpl.maintenance_type_id is None)):
                    pass  # generic templates have no type constraint
                # Template's maintenance type must match the MO's type
                # when this TT is used. Not enforced here since the TT
                # doesn't have a maintenance_type itself -- the check
                # happens at MO-create time.
            tt.default_scope_template_id = default_scope_template_id
        db.session.commit()
        return tt

    def get_by_id(self, tt_id):
        return db.session.get(TransactionType, tt_id)

    def list(self, order_category=None, include_inactive=False):
        q = TransactionType.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if order_category:
            q = q.filter_by(order_category=order_category)
        return q.order_by(TransactionType.group, TransactionType.sort_order).all()

    def deactivate(self, tt_id):
        tt = self.get_by_id(tt_id)
        if tt:
            tt.is_active = False
            db.session.commit()

    def reactivate(self, tt_id):
        tt = self.get_by_id(tt_id)
        if tt:
            tt.is_active = True
            db.session.commit()


def _part_line_description(part):
    """One readable PR line from a part, carrying the detail the buyer
    needs. Part number and specification are folded into the single
    description field because that is all a PR line has -- dropping them
    would send someone shopping with less information than the mechanic
    actually provided."""
    bits = [part.part_description]
    if part.part_number:
        bits.append(f"(P/N {part.part_number})")
    if part.specification:
        bits.append(f"- {part.specification}")
    if part.uom:
        bits.append(f"[{part.uom}]")
    return " ".join(bits)[:255]


class MaintenanceOrderService(BaseTransactionService):
    model = MaintenanceOrder
    document_type_code = "MO"
    reference_table = "maintenance_orders"
    date_fields = (("scheduled_date", "Scheduled Date"),
                   ("completed_date", "Completed Date"),
                   ("created_at", "Created Date"))
    # Matches exactly what maintenanceorder_list.html accesses per row
    # (o.vehicle, o.maintenance_type, o.transaction_type) -- without
    # this, a list of N orders triggered up to 3*N extra lazy-load
    # queries just to render the list page.
    list_eager_load = ["vehicle", "maintenance_type", "transaction_type"]

    def create(self, *, vehicle_id, scheduled_date, user,
               order_category="MAINTENANCE", maintenance_type_id=None,
               transaction_type_id=None, scope_template_id=None,
               pm_schedule_id=None, description=None, odometer_at_service=None,
               assigned_mechanic=None, vendor_id=None, estimated_cost=None,
               driver_id=None, destination_branch_id=None,
               disposal_value=None, disposal_recipient=None,
               assignment_classification=None, scope_items=None):
        """Create a DRAFT order.

        scope_items -- optional list of Scope of Work lines (see
        normalize_scope_rows). When supplied (even as an empty list) it
        IS the order's scope: the form pre-fills from the chosen template
        and the user may have edited it, so the edited lines win over a
        fresh template copy. When omitted (None), the template's items
        are copied exactly as before -- every existing caller keeps its
        behaviour. Valid for every order category.
        """
        from app.modules.master_data.vehicle.models import Vehicle
        # Validated before anything is written: a bad line must not leave
        # a numbered, half-built order behind.
        manual_rows = (normalize_scope_rows(scope_items)
                       if scope_items is not None else None)
        vehicle = db.session.get(Vehicle, vehicle_id)
        # A vehicle must not have two PM orders open at once: if a PMS is
        # already DRAFT / awaiting approval / IN_PROGRESS, a second one
        # for the same maintenance type would double-book the workshop
        # and double-count the service against the PM schedule. The
        # auto-generation task already refused to create a duplicate for
        # exactly this reason, but the MANUAL create path had no such
        # guard, so the same vehicle could be queued twice by hand.
        if order_category == "MAINTENANCE" and maintenance_type_id:
            self._check_no_open_duplicate(vehicle_id, maintenance_type_id)
        # Snapshot the vehicle's CURRENT branch as the "From" for the
        # Asset Transfer Report -- must be captured now, at creation,
        # since completion will update Vehicle.branch_id to the
        # destination, and by then the vehicle's current branch would
        # already be the new one, not the "From" branch being reported.
        origin_branch_id = (vehicle.branch_id if destination_branch_id
                           and vehicle else None)
        self._check_destination(destination_branch_id, origin_branch_id)
        # Assignment / transfer / disposal often have no money line.
        # Approval routing uses estimated_cost; leave it NULL and the
        # matrix looks for "no amount" only. Default operational drafts
        # to 0 so a 0-and-up band matches.
        if estimated_cost is None and order_category == "OPERATIONAL":
            from decimal import Decimal
            estimated_cost = Decimal("0")
        # Per the spec: "Not every Maintenance Order represents vehicle
        # maintenance. Administrative, Deployment, Disposal, and
        # Accessories are valid Maintenance Orders" — Operational orders
        # are normal work requests and must NOT require a Maintenance
        # Type or PM Scope Template at all. MAINTENANCE-category orders
        # keep the exact original requirement (nothing changes for
        # existing PM/CM callers).
        if order_category == "OPERATIONAL":
            maintenance_type_id = None
            scope_template_id = None
            pm_schedule_id = None
        elif not maintenance_type_id:
            raise InvalidOrderCategoryError(
                "Maintenance Type is required for a Maintenance-category order.")

        if transaction_type_id:
            tt = db.session.get(TransactionType, transaction_type_id)
            if tt and tt.order_category != order_category:
                raise InvalidOrderCategoryError(
                    f"Transaction Type '{tt.name}' belongs to the "
                    f"{tt.order_category} category, not {order_category}.")

        mtype = (db.session.get(MaintenanceType, maintenance_type_id)
                if maintenance_type_id else None)

        numbering = AutoNumberingService()
        doc_number = None
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            pass  # never block creation for a numbering-scheme failure

        def _build_order(number):
            return MaintenanceOrder(
                document_number=number, vehicle_id=vehicle_id,
                order_category=order_category, transaction_type_id=transaction_type_id,
                maintenance_type_id=maintenance_type_id,
                category=mtype.category if mtype else None,
                pm_schedule_id=pm_schedule_id,
                scope_template_id=scope_template_id, description=description,
                odometer_at_service=odometer_at_service,
                scheduled_date=scheduled_date,
                assigned_mechanic=assigned_mechanic, vendor_id=vendor_id,
                estimated_cost=estimated_cost, status="DRAFT",
                driver_id=driver_id, destination_branch_id=destination_branch_id,
                origin_branch_id=origin_branch_id,
                disposal_value=disposal_value, disposal_recipient=disposal_recipient,
                assignment_classification=assignment_classification,
                requested_by=user.id if user else None)

        # Retried ONLY on a genuine document-number collision -- the
        # reported bug: under concurrent submission the counter can
        # hand out a number that a near-simultaneous request already
        # committed, and the previous code let that reach the user as
        # an unhandled 500 rather than quietly moving on to the next
        # real number. Confirmed as a numbering collision (not some
        # other constraint failing) by checking whether that exact
        # number is already taken before retrying -- an unrelated
        # failure surfaces immediately instead of retrying against a
        # wall it can't get past.
        order = _build_order(doc_number)
        db.session.add(order)
        for attempt in range(3):
            try:
                db.session.flush()
                break
            except IntegrityError:
                db.session.rollback()
                if (doc_number is None
                        or not MaintenanceOrder.query.filter_by(
                            document_number=doc_number).first()):
                    raise
                try:
                    doc_number = numbering.generate(self.document_type_code)
                except Exception:
                    doc_number = None
                order = _build_order(doc_number)
                db.session.add(order)
        else:
            db.session.flush()  # let the final attempt's error surface plainly

        if scope_template_id:
            # Fetched by ID alone before this check existed -- nothing
            # verified it actually belonged to the maintenance_type
            # also on the request. A tampered request (or a client
            # bug) could pair a vehicle/maintenance_type with a scope
            # template built for an entirely different maintenance
            # discipline and have it silently accepted, copying the
            # wrong checklist onto the order. Checked here, not just
            # trusted from whatever the frontend sent, since a
            # disabled/locked field in the UI is only ever a UX
            # convenience -- someone editing the request body directly
            # bypasses it completely.
            template = db.session.get(PMScopeTemplate, scope_template_id)
            if template is None:
                raise InvalidOrderCategoryError(
                    "That PM Scope Template does not exist.")
            if template.maintenance_type_id != maintenance_type_id:
                raise InvalidOrderCategoryError(
                    "That PM Scope Template does not match this order's "
                    "Maintenance Type.")
            if manual_rows is None:
                self._copy_template_items(order, template)

        if manual_rows is not None:
            # Lines supplied by the caller -- any category, template or
            # not. A line keeps the client's TEMPLATE hint only when the
            # order really has a template (an OPERATIONAL order has its
            # template nulled above, so its lines are always MANUAL).
            has_template = bool(order.scope_template_id)
            for pos, row in enumerate(manual_rows, start=1):
                origin = ("TEMPLATE" if has_template
                          and row["origin"] == "TEMPLATE" else "MANUAL")
                order.checklist_items.append(MaintenanceChecklistItem(
                    activity_code=row["activity_code"],
                    activity_description=row["activity_description"],
                    sort_order=pos, origin=origin))

        db.session.commit()
        return order

    @staticmethod
    def _copy_template_items(order, template) -> None:
        """Snapshot a PM Scope Template's items onto the order. A copy:
        the template is read, never written."""
        for item in template.items:
            order.checklist_items.append(MaintenanceChecklistItem(
                activity_code=item.activity_code or None,
                activity_description=item.activity_description,
                sort_order=item.sort_order, origin="TEMPLATE"))

    def replace_scope(self, order_id: int, rows, user=None):
        """Replace an order's Scope of Work with `rows`, in order.

        Allowed only while editable_scope() is "FULL" -- a Draft never
        submitted, or one an approver Returned. Once it is with an
        approver (PENDING) or approved, the scope is what was approved
        and must not move underneath it.

        Diffed by line id rather than delete-all/insert-all, so the audit
        trail records an UPDATE for a corrected line instead of a
        delete-and-recreate, and a line keeps its identity (and origin)
        across edits:

          * a row with an `id` belonging to this order -> updated in place
          * a row without an `id`                      -> new MANUAL line
          * an existing line not in `rows`             -> deleted

        The master PM Scope Template is never touched.
        """
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")
        if self.editable_scope(order) != "FULL":
            if order.status == "DRAFT":
                raise InvalidOrderStateError(
                    "This order is awaiting approval, so its Scope of Work "
                    "is locked. Ask the approver to return it if it needs "
                    "changes.")
            raise InvalidOrderStateError(
                f"The Scope of Work of a {order.status} order can no "
                f"longer be changed.")

        clean = normalize_scope_rows(rows)
        existing = {i.id: i for i in order.checklist_items}
        for row in clean:
            if row["id"] is not None and row["id"] not in existing:
                raise ScopeValidationError(
                    "A Scope of Work line does not belong to this order.")

        keep_ids = {r["id"] for r in clean if r["id"] is not None}
        for item in list(order.checklist_items):
            if item.id not in keep_ids:
                order.checklist_items.remove(item)  # delete-orphan

        for pos, row in enumerate(clean, start=1):
            if row["id"] is not None:
                item = existing[row["id"]]
                item.activity_code = row["activity_code"]
                item.activity_description = row["activity_description"]
                item.sort_order = pos
            else:
                order.checklist_items.append(MaintenanceChecklistItem(
                    activity_code=row["activity_code"],
                    activity_description=row["activity_description"],
                    sort_order=pos, origin="MANUAL"))
        order.updated_by = user.id if user else None
        db.session.commit()
        # Relationship-changing write with expire_on_commit=False: drop
        # the cached collection so the caller reads the real rows.
        db.session.expire(order)
        return order

    def editable_scope(self, order) -> str:
        """How much of this order may be edited right now.

        Returns "FULL" or "NONE". ("REMARKS_ONLY" was retired by the
        2026-10-12 edit policy; callers still tolerate it.)

        Spec: docs/superpowers/specs/2026-10-12-document-edit-policy-design.md

          * DRAFT -- never submitted, approval PENDING, RETURNED or
            APPROVED-awaiting-start                         -> FULL
          * IN_PROGRESS                                     -> FULL
          * approval REJECTED                               -> NONE
          * COMPLETED / CANCELLED / anything else           -> NONE

        Editing while approval is PENDING or after it is APPROVED was
        locked before; the initiator then had no way to fix a wrong
        vendor or destination branch short of asking for a return.
        Approval is deliberately NOT restarted by an edit (Michael's
        decision). What protects the approver instead is that every
        post-submission change is notified (edited_after_submission)
        and listed on the order (changes_since_submission).
        """
        if order is None:
            return "NONE"
        if order.status not in ("DRAFT", "IN_PROGRESS"):
            return "NONE"
        instance = self._latest_instance(order)
        if instance is not None and instance.status == "REJECTED":
            return "NONE"
        return "FULL"

    def _latest_instance(self, order):
        """The order's approval instance. Submit stores
        approval_instance_id, but a stale session or an instance created
        only on the approval table must still count, so fall back to the
        latest instance by reference."""
        instance = order.approval_instance
        if instance is None:
            from app.core.approval.models import ApprovalInstance
            instance = (ApprovalInstance.query
                        .filter_by(reference_table="maintenance_orders",
                                   reference_id=order.id)
                        .order_by(ApprovalInstance.id.desc())
                        .first())
        return instance

    def can_edit(self, order, user) -> bool:
        """May THIS user edit THIS order now?

        The order must be open (editable_scope FULL), and the user must
        be its initiator or hold maintenanceorder.update. The initiator
        rule is what lets a requester correct their own request without
        being granted edit rights over everyone else's orders.
        """
        if user is None or self.editable_scope(order) != "FULL":
            return False
        if order.requested_by is not None and order.requested_by == user.id:
            return True
        return user.has_permission("maintenanceorder.update")

    def _check_no_open_duplicate(self, vehicle_id, maintenance_type_id,
                                 exclude_order_id=None):
        """A vehicle must not have two open orders of the same
        maintenance type: it would double-book the workshop and
        double-count the service against the PM schedule. Applies on
        create and when an edit moves an order to another vehicle or
        maintenance type."""
        if not maintenance_type_id:
            return
        q = (MaintenanceOrder.query
             .filter_by(vehicle_id=vehicle_id,
                        maintenance_type_id=maintenance_type_id)
             .filter(MaintenanceOrder.status.notin_(
                 ["COMPLETED", "CANCELLED"])))
        if exclude_order_id is not None:
            q = q.filter(MaintenanceOrder.id != exclude_order_id)
        existing_open = q.first()
        if existing_open:
            raise InvalidOrderStateError(
                f"This vehicle already has an open "
                f"{existing_open.maintenance_type.name if existing_open.maintenance_type else 'maintenance'} "
                f"order ({existing_open.document_number or 'draft'}, "
                f"status {existing_open.status}). Complete or cancel it "
                f"before creating another one.")

    @staticmethod
    def _check_destination(destination_branch_id, origin_branch_id):
        """A transfer to the branch the vehicle is already at moves
        nothing and would still issue an ATR number."""
        if destination_branch_id and destination_branch_id == origin_branch_id:
            raise OrderFieldError(
                "Destination branch must be different from the vehicle's "
                "current branch (the origin).", "destination_branch_id")

    @staticmethod
    def _check_type_change(order, fields):
        """The transaction type may move only within its own group
        (Transfer <-> Relocation, Scrappage -> Sold ...). Another group
        means a different field set, approval matrix and ATR/ADR series
        -- that is a new order, not a correction."""
        new_id = fields.get("transaction_type_id")
        if not new_id or new_id == order.transaction_type_id:
            return
        new = db.session.get(TransactionType, new_id)
        if new is None:
            raise OrderFieldError("That transaction type does not exist.",
                                  "transaction_type_id")
        old = order.transaction_type
        same_category = new.order_category == order.order_category
        same_group = old is None or (
            (new.group or "").upper() == (old.group or "").upper())
        if not (same_category and same_group):
            group = (old.group if old else order.order_category) or ""
            raise OrderFieldError(
                f"The transaction type can only be changed to another "
                f"type in the same group ({group.title()}). To change "
                f"what kind of order this is, cancel it and raise a new "
                f"one.", "transaction_type_id")

    def update(self, order_id: int, *, user=None, **fields):
        """Update an order within whatever scope its state allows.

        Every change is captured by the existing audit listeners, so who
        changed what is recorded without anything extra here.
        """
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")

        scope = self.editable_scope(order)
        if scope == "NONE":
            instance = self._latest_instance(order)
            if instance is not None and instance.status == "REJECTED":
                raise InvalidOrderStateError(
                    "This order was rejected and can no longer be edited.")
            raise InvalidOrderStateError(
                f"A {order.status} order can no longer be edited.")
        # State first, then permission: a closed order is closed for
        # everybody (409), whereas the permission answer (403) only
        # means something while the order is still open.
        if user is not None and not self.can_edit(order, user):
            raise OrderEditForbiddenError(
                "Only the person who raised this order, or a user with the "
                "Maintenance Order Update permission, can edit it.")

        editable = {
            "description", "scheduled_date", "odometer_at_service",
            "estimated_cost", "assigned_mechanic", "vendor_id",
            "maintenance_type_id", "transaction_type_id",
            "scope_template_id", "pm_schedule_id", "driver_id",
            "destination_branch_id", "assignment_classification",
            "disposal_value", "disposal_recipient", "vehicle_id",
        }
        fields = {k: v for k, v in fields.items() if k in editable}

        # Everything is validated against the values the order WILL have,
        # before any attribute is set: a refused edit must not leave a
        # half-changed order dirty in the session.
        self._check_type_change(order, fields)
        from app.modules.master_data.vehicle.models import Vehicle
        target_vehicle_id = fields.get("vehicle_id", order.vehicle_id)
        target_vehicle = db.session.get(Vehicle, target_vehicle_id)
        if target_vehicle is None:
            raise OrderFieldError("That vehicle does not exist.",
                                  "vehicle_id")
        target_mtype_id = fields.get("maintenance_type_id",
                                     order.maintenance_type_id)
        if (("vehicle_id" in fields or "maintenance_type_id" in fields)
                and order.order_category == "MAINTENANCE"):
            self._check_no_open_duplicate(target_vehicle_id,
                                          target_mtype_id,
                                          exclude_order_id=order.id)
        target_dest = fields.get("destination_branch_id",
                                 order.destination_branch_id)
        # Origin is the vehicle's branch NOW, not when the order was
        # raised: MO-2026-000046 showed Origin Muntinlupa for a vehicle
        # that had since moved to Manila Hub.
        target_origin = target_vehicle.branch_id if target_dest else None
        self._check_destination(target_dest, target_origin)
        # Changing the template on an editable order re-seeds its Scope
        # of Work from the new template. Before this, update() set
        # scope_template_id and left the OLD template's lines in place,
        # so an order could claim template B while carrying template A's
        # checklist. Only on an actual CHANGE: the edit form re-posts the
        # current template id on every save, and that must not wipe lines
        # the user edited. Clearing the template keeps the lines (they
        # may be manual).
        reseed_template = None
        if "scope_template_id" in fields and scope == "FULL":
            new_tid = fields["scope_template_id"]
            if new_tid and new_tid != order.scope_template_id:
                reseed_template = db.session.get(PMScopeTemplate, new_tid)
                if reseed_template is None:
                    raise InvalidOrderCategoryError(
                        "That PM Scope Template does not exist.")
                target_mtype = fields.get("maintenance_type_id",
                                          order.maintenance_type_id)
                if reseed_template.maintenance_type_id != target_mtype:
                    raise InvalidOrderCategoryError(
                        "That PM Scope Template does not match this "
                        "order's Maintenance Type.")

        # Compared by value: the edit form re-posts every field, and
        # Decimal("15000.00") == 15000 must not count as a change.
        changed = [k for k, v in fields.items() if getattr(order, k) != v]
        for key, value in fields.items():
            setattr(order, key, value)
        order.origin_branch_id = target_origin
        if "maintenance_type_id" in fields:
            mtype = (db.session.get(MaintenanceType, target_mtype_id)
                     if target_mtype_id else None)
            order.category = mtype.category if mtype else None
        if reseed_template is not None:
            for item in list(order.checklist_items):
                order.checklist_items.remove(item)
            self._copy_template_items(order, reseed_template)
        order.updated_by = user.id if user else None
        db.session.commit()
        if reseed_template is not None:
            db.session.expire(order)
        if changed:
            self._notify_edit_after_submission(order)
        return order

    def _notify_edit_after_submission(self, order):
        """Approval is not restarted by an edit (Michael's decision), so
        the people it concerns are told instead -- through the
        Notification Engine's configurable rules (seeded: the current
        approver and everyone who already approved).

        Only once someone other than the initiator is relying on the
        order: approval PENDING or APPROVED, or work IN_PROGRESS. A
        Returned order is being fixed at the approver's request; they
        see it again on resubmit."""
        instance = self._latest_instance(order)
        if instance is None:
            return
        if not (instance.status in ("PENDING", "APPROVED")
                or order.status == "IN_PROGRESS"):
            return
        from app.core.approval.engine import publish
        publish("edited_after_submission", instance)

    #: Fields shown in the changed-after-submission list, in form order.
    #: The editable business fields only: status moves, the origin
    #: re-snapshot and bookkeeping columns are not edits.
    _CHANGE_LABELS = {
        "vehicle_id": "Vehicle",
        "transaction_type_id": "Transaction type",
        "maintenance_type_id": "Maintenance type",
        "scope_template_id": "PM scope template",
        "scheduled_date": "Scheduled date",
        "odometer_at_service": "Odometer",
        "vendor_id": "Vendor",
        "assigned_mechanic": "Assigned mechanic",
        "estimated_cost": "Estimated cost",
        "driver_id": "Driver",
        "assignment_classification": "Assignment classification",
        "destination_branch_id": "Destination branch",
        "disposal_value": "Disposal value",
        "disposal_recipient": "Disposal recipient",
        "description": "Description / remarks",
    }

    def changes_since_submission(self, order) -> list:
        """Every edit made to the order after it was first submitted,
        newest first: [{field, label, old, new, changed_by, changed_at}].

        Read from the existing Audit Trail rather than a new table --
        every field change is already logged there with who and when.
        Edits made while Returned are included too: they are part of
        what changed since the approver first looked."""
        from app.core.approval.models import ApprovalAction, ApprovalInstance
        from app.core.models.audit_log import AuditLog
        from app.modules.user_management.models import User
        instance_ids = [i.id for i in ApprovalInstance.query.filter_by(
            reference_table="maintenance_orders", reference_id=order.id)]
        if not instance_ids:
            return []
        first = (ApprovalAction.query
                 .filter(ApprovalAction.instance_id.in_(instance_ids),
                         ApprovalAction.action == "SUBMIT")
                 .order_by(ApprovalAction.acted_at.asc()).first())
        if first is None:
            return []
        since = _naive_utc(first.acted_at)
        rows = (AuditLog.query
                .filter_by(table_name="maintenance_orders",
                           record_id=order.id, action="UPDATE")
                .order_by(AuditLog.id.desc()).all())
        users = {}
        out = []
        for row in rows:
            if _naive_utc(row.timestamp) < since:
                continue
            old, new = row.old_values or {}, row.new_values or {}
            for field, label in self._CHANGE_LABELS.items():
                # The audit listener only records attributes whose value
                # actually changed, so presence in new_values is enough.
                if field not in new:
                    continue
                if row.user_id and row.user_id not in users:
                    u = db.session.get(User, row.user_id)
                    users[row.user_id] = u.full_name if u else None
                out.append({
                    "field": field, "label": label,
                    "old": _change_display(field, old.get(field)),
                    "new": _change_display(field, new.get(field)),
                    "changed_by": users.get(row.user_id),
                    "changed_at": _naive_utc(row.timestamp).isoformat(),
                })
        return out

    def start_work(self, order_id: int):
        """Move an approved order into IN_PROGRESS.

        The approval gate is enforced HERE, not only in the template:
        the detail page hides the Start Work button until the order is
        approved, but a hidden button is not a control -- a direct POST
        would otherwise let work begin on an unsubmitted or
        still-pending order, skipping the approval step entirely.

        Note that when a document type is configured as NOT requiring
        approval, the Approval Engine still creates an instance and
        marks it APPROVED immediately on submit, so this single check
        works for both approval-required and no-approval setups. What it
        correctly rejects is an order that was never submitted at all.
        """
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")
        if order.status != "DRAFT":
            raise InvalidOrderStateError(
                f"Work can only be started on a DRAFT order "
                f"(this one is {order.status}).")
        instance = order.approval_instance
        if instance is None:
            raise InvalidOrderStateError(
                "This order has not been submitted yet. Submit it for "
                "approval before starting work.")
        if instance.status != "APPROVED":
            raise InvalidOrderStateError(
                f"This order is not approved yet (approval status: "
                f"{instance.status}). Work can only start once it is "
                f"approved.")
        order.status = "IN_PROGRESS"
        db.session.commit()
        return order

    def save_scope_as_template(self, order_id: int, *,
                                name: str,
                                description: str | None = None):
        """Promote the order's current Scope of Work rows to a new
        PM Scope Template.

        The resulting template is a snapshot of the ORDER's own rows
        (the same ones that appear on the detail screen), not a copy of
        any master template. So manual edits made after creation are
        preserved, and the master template is never written.

        Requires:
          - the order is a MAINTENANCE-category order (it has a
            maintenance_type_id);
          - the order has at least one checklist row.
          - name is non-blank.

        The template is created with the order's maintenance_type_id and
        with pm_schedule_id=None (generic, not tied to a specific PM
        schedule), so it appears in the scope-template picker for any
        order of the same maintenance type.

        Permission is checked at the API layer (pmscopetemplate.create),
        not here.
        """
        from app.modules.maintenance_config.models import (
            PMScopeTemplate, PMScopeItem)
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")
        if order.order_category != "MAINTENANCE" or not order.maintenance_type_id:
            raise InvalidOrderCategoryError(
                "Only Maintenance-category orders can create a PM Scope "
                "Template (Operational orders have no maintenance type).")
        name = (name or "").strip()
        if not name:
            raise ScopeValidationError("name is required.")
        if not order.checklist_items:
            raise InvalidOrderStateError(
                "This order has no activities -- save some scope lines "
                "before saving as a template.")
        tmpl = PMScopeTemplate(
            maintenance_type_id=order.maintenance_type_id,
            name=name, description=description or None,
            pm_schedule_id=None)
        db.session.add(tmpl)
        for item in sorted(order.checklist_items,
                           key=lambda i: (i.sort_order or 0, i.id or 0)):
            tmpl.items.append(PMScopeItem(
                # activity_code is nullable in PMScopeItem as of the
                # mo_phase_b migration; preserve None for manual lines.
                activity_code=item.activity_code or None,
                activity_description=item.activity_description,
                sort_order=item.sort_order or 0,
                standard_labor_hours=None,
                estimated_cost=None,
                required_parts=None,
                vendor_recommendation=None))
        db.session.commit()
        return tmpl

    def toggle_checklist_item(self, item_id: int, done: bool, user):
        item = db.session.get(MaintenanceChecklistItem, item_id)
        order = item.order
        if order.status != "IN_PROGRESS":
            raise InvalidOrderStateError(
                "Checklist items can only be updated while the order is "
                "IN_PROGRESS.")
        item.is_done = done
        item.done_by = user.id if done and user else None
        item.done_at = datetime.now(timezone.utc) if done else None
        db.session.commit()
        return item

    def mark_all_checklist_items(self, order_id: int, done: bool, user):
        """Bulk toggle every checklist item on an order in one commit --
        backs the "Mark All Done" button so a long PM scope (dozens of
        lines) doesn't need one click + page round-trip per item."""
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")
        if order.status != "IN_PROGRESS":
            raise InvalidOrderStateError(
                "Checklist items can only be updated while the order is "
                "IN_PROGRESS.")
        now = datetime.now(timezone.utc)
        for item in order.checklist_items:
            item.is_done = done
            item.done_by = user.id if done and user else None
            item.done_at = now if done else None
        db.session.commit()
        return order.checklist_items

    def complete(self, order_id: int, actual_cost, completed_date):
        order = db.session.get(MaintenanceOrder, order_id)
        # "PM" is the current code for Preventive Maintenance (Category
        # Lookup, admin-configurable) — "PREVENTIVE" is kept here too for
        # any row that predates the category-code migration and wasn't
        # translated for some reason, so the rule doesn't silently stop
        # applying to it.
        if order.category in ("PM", "PREVENTIVE") and order.checklist_items:
            incomplete = [i for i in order.checklist_items if not i.is_done]
            if incomplete:
                raise IncompleteChecklistError(
                    f"{len(incomplete)} checklist item(s) still incomplete; "
                    "all items must be done before completing a Preventive "
                    "Maintenance order.")
        # Coerce to Decimal rather than storing whatever the form sent.
        # The route passes request.form values straight through, so this
        # arrives as a STRING, and SQLAlchemy does not coerce a Numeric
        # column until flush -- so anything reading order.actual_cost in
        # the same request gets a str. The print template formats it as
        # money ("{:,.2f}"), which raised
        #   ValueError: Unknown format code 'f' for object of type 'str'
        # and 500'd the printout immediately after completing an order.
        if actual_cost is not None and not isinstance(actual_cost, Decimal):
            try:
                actual_cost = Decimal(str(actual_cost))
            except (InvalidOperation, ValueError):
                actual_cost = None
        from app.modules.transactions.maintenance_invoice.models import (
            MaintenanceInvoice)
        invoices = (MaintenanceInvoice.query
                    .filter_by(maintenance_order_id=order.id)
                    .filter(MaintenanceInvoice.status != "CANCELLED")
                    .all())
        invoice_total = sum((i.total_invoice_amount or 0) for i in invoices)
        # Blank / 0 on Mark Complete means "use the billed total", not
        # store zero against an order that has ₱8,008 of invoices.
        if actual_cost is None or actual_cost == 0:
            actual_cost = invoice_total or Decimal("0")
        order.actual_cost = actual_cost
        order.completed_date = completed_date
        order.status = "COMPLETED"
        for inv in invoices:
            inv.status = "COMPLETED"
        if order.odometer_at_service and (
                order.vehicle.current_odometer is None or
                order.odometer_at_service > order.vehicle.current_odometer):
            order.vehicle.current_odometer = order.odometer_at_service
        db.session.commit()

        # "Vehicle Assignment Memo" workflow: an Operational order with a
        # Driver/Assignee set (Assignment, Reassignment, Relocation,
        # Transfer transaction types) updates the vehicle's current
        # assigned driver on completion -- the same outcome an approved
        # ATD produces (see assignment_hooks.py), so either document can
        # be the operative record for a given handover.
        if order.driver_id:
            from app.modules.master_data.vehicle.assignment_hooks import (
                assign_driver_to_vehicle)
            assign_driver_to_vehicle(order.vehicle_id, order.driver_id,
                                     source="MO",
                                     source_table="maintenance_orders",
                                     source_id=order.id)

        # "Asset Transfer Report" workflow: an Operational order with a
        # Destination Branch set (Relocation/Transfer transaction types)
        # moves the vehicle to that branch on completion, and generates
        # a dedicated ATR-2026-NNNN reference number the first time it's
        # completed (not regenerated on any later re-save).
        if order.destination_branch_id:
            from app.modules.master_data.vehicle.assignment_hooks import (
                transfer_vehicle_branch)
            # The true "From" is where the vehicle is at the moment it
            # moves. Only on the first completion (no ATR yet): a re-save
            # after the move would otherwise record the destination as
            # the origin.
            if not order.transfer_reference_number and order.vehicle:
                order.origin_branch_id = order.vehicle.branch_id
            transfer_vehicle_branch(order.vehicle_id, order.destination_branch_id)
            if not order.transfer_reference_number:
                from app.core.numbering.numbering_service import (
                    AutoNumberingService)
                order.transfer_reference_number = (
                    AutoNumberingService().generate("ATR"))
                db.session.commit()

        # "Asset Disposal Report" workflow: completing an order whose
        # Transaction Type belongs to the DISPOSAL group retires the
        # vehicle (status -> DISPOSED, already correctly excluded from
        # PM/registration due-calculations and hidden from active lists
        # elsewhere) and generates a dedicated ADR-2026-NNNN reference
        # number, the retirement-stage counterpart to the Asset Transfer
        # Report's ATR No. The disposal REASON/METHOD is the transaction
        # type itself (Scrappage/Carnapped/Total Loss/Uneconomical/Sold/
        # Donated) -- disposal_value/disposal_recipient only add what the
        # transaction type alone can't capture.
        if (order.transaction_type
                and order.transaction_type.group == "DISPOSAL"):
            order.vehicle.status = "DISPOSED"
            if not order.disposal_reference_number:
                from app.core.numbering.numbering_service import (
                    AutoNumberingService)
                order.disposal_reference_number = (
                    AutoNumberingService().generate("ADR"))
            db.session.commit()

        return order

    def maintenance_class_clause(self, wanted):
        """SQL matching MaintenanceOrder.maintenance_class_bucket.

        The Classification shown in the list is NOT a stored column --
        it is derived, and the derivation has three branches. Filtering
        on the unrelated `category` column instead returned nothing at
        all for every selection, which is exactly what was reported.

        Mirrors maintenance_class_bucket() branch for branch:
          * order_category == OPERATIONAL            -> OPERATIONAL
          * transaction_type.maintenance_class set   -> that value
          * no transaction type recorded at all      -> PREVENTIVE
            (older PM orders predating transaction types)
          * otherwise                                -> UNCLASSIFIED

        A test asserts this clause and the property agree on the same
        real rows, so the two cannot quietly drift apart.
        """
        from sqlalchemy import and_, or_, not_
        from app.modules.transactions.maintenance_order.models import (
            TransactionType)

        if not wanted:
            return None

        if wanted == "OPERATIONAL":
            return MaintenanceOrder.order_category == "OPERATIONAL"

        not_operational = or_(
            MaintenanceOrder.order_category != "OPERATIONAL",
            MaintenanceOrder.order_category.is_(None))

        typed = MaintenanceOrder.transaction_type_id.in_(
            db.session.query(TransactionType.id)
            .filter(TransactionType.maintenance_class == wanted))

        if wanted == "PREVENTIVE":
            # An order with no transaction type at all counts as
            # preventive, matching the property.
            typed = or_(typed, MaintenanceOrder.transaction_type_id.is_(None))

        if wanted == "UNCLASSIFIED":
            # Has a transaction type, but that type carries no class.
            typed = and_(
                MaintenanceOrder.transaction_type_id.isnot(None),
                MaintenanceOrder.transaction_type_id.in_(
                    db.session.query(TransactionType.id)
                    .filter(or_(TransactionType.maintenance_class.is_(None),
                               TransactionType.maintenance_class == ""))))

        return and_(not_operational, typed)

    # ── Parts to procure, and the Purchase Request they become ──────

    def add_part(self, order_id, *, part_description, quantity=1,
                estimated_unit_cost=0, part_number=None, specification=None,
                uom=None, remarks=None):
        """Record a part the mechanic has determined needs procuring.

        Only while the order is still DRAFT: the parts list is what the
        approver approves and what the Purchase Request is built from,
        so it must not change after that decision has been made.
        """
        from decimal import Decimal
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrderPart)
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")
        self._require_parts_editable(order)
        if not str(part_description or "").strip():
            raise InvalidOrderStateError("Part description is required.")

        part = MaintenanceOrderPart(
            order_id=order.id,
            part_number=(part_number or None),
            part_description=part_description.strip(),
            specification=(specification or None),
            uom=(uom or None),
            quantity=Decimal(str(quantity or 1)),
            estimated_unit_cost=Decimal(str(estimated_unit_cost or 0)),
            remarks=(remarks or None),
            sort_order=len(order.parts))
        # Append to the relationship rather than db.session.add(part):
        # adding the row alone leaves the parent's already-loaded parts
        # collection stale, so anything reading order.parts later in the
        # same session sees an empty list. That mattered concretely --
        # PR generation checks `if not order.parts` and would have
        # silently produced nothing.
        order.parts.append(part)
        db.session.commit()
        return part

    def _require_parts_editable(self, order):
        """Parts follow the order's edit scope, with one exception: once
        a Purchase Request has been raised from them, the PR is the
        procurement record and the two must not drift apart."""
        if self.editable_scope(order) != "FULL":
            raise InvalidOrderStateError(
                f"A {order.status} order can no longer be edited.")
        if order.purchase_request_id:
            pr = order.purchase_request
            number = getattr(pr, "document_number", None) or "the PR"
            raise InvalidOrderStateError(
                f"A Purchase Request ({number}) has already been raised "
                f"from these parts. Change the items on the Purchase "
                f"Request instead.")

    def remove_part(self, part_id):
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrderPart)
        part = db.session.get(MaintenanceOrderPart, part_id)
        if part is None:
            return None
        order = part.order
        self._require_parts_editable(order)
        # Remove via the relationship, not db.session.delete(part): the
        # delete-orphan cascade still removes the row, but this also
        # keeps the parent's already-loaded parts collection correct.
        # Symmetric to the same fix in add_part -- without it the
        # running total recalculated straight afterwards still counted
        # the part that was just removed.
        order.parts.remove(part)
        db.session.commit()
        return part

    def generate_purchase_request(self, order_id, user):
        """Create a DRAFT Purchase Request from this order's parts list.

        The whole point is to remove the double encoding: the mechanic
        already typed each part on the Maintenance Order, so those exact
        items and descriptions become the PR lines rather than being
        re-keyed by someone else into a second form.

        Deliberately left as a DRAFT and NOT submitted. The Fleet person
        still owns that decision -- they may need to attach a quotation,
        adjust a quantity, name a supplier, or check the budget first.
        Auto-submitting would take that judgement away and push an
        unreviewed request into somebody's approval queue.

        Returns None (rather than raising) when the order has no parts:
        plenty of maintenance work needs no procurement at all, and that
        is a normal outcome, not an error.
        """
        order = db.session.get(MaintenanceOrder, order_id)
        if order is None:
            raise InvalidOrderStateError("Maintenance Order not found.")
        if not order.parts:
            return None
        if order.purchase_request_id:
            # Idempotent: approval events can fire more than once, and a
            # duplicate PR for the same order would be a real problem to
            # unpick downstream.
            return order.purchase_request

        from app.modules.transactions.purchase_request.service import (
            PurchaseRequestService)

        vehicle_label = "—"
        if order.vehicle:
            vehicle_label = (order.vehicle.plate_number
                            or order.vehicle.conduction_number or "—")

        lines = [{
            "item_description": _part_line_description(p),
            "quantity": p.quantity,
            "unit_cost": p.estimated_unit_cost,
        } for p in sorted(order.parts, key=lambda x: x.sort_order)]

        pr = PurchaseRequestService().create(
            description=(f"Parts for {order.document_number or 'Maintenance Order'} "
                        f"— {vehicle_label}"),
            justification=(order.description or None),
            # The cost centre chain established earlier resolves through
            # the vehicle's department, so the PR lands against the right
            # department without anyone re-selecting it.
            department_id=(order.vehicle.department_id
                          if order.vehicle else None),
            lines=lines, user=user)

        order.purchase_request_id = pr.id
        db.session.commit()
        return pr