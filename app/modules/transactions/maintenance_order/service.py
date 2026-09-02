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
    def create(self, *, code, name, order_category, group=None, sort_order=0):
        tt = TransactionType(code=code, name=name, order_category=order_category,
                             group=group, sort_order=sort_order)
        db.session.add(tt)
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
               assignment_classification=None):
        from app.modules.master_data.vehicle.models import Vehicle
        vehicle = db.session.get(Vehicle, vehicle_id)
        # A vehicle must not have two PM orders open at once: if a PMS is
        # already DRAFT / awaiting approval / IN_PROGRESS, a second one
        # for the same maintenance type would double-book the workshop
        # and double-count the service against the PM schedule. The
        # auto-generation task already refused to create a duplicate for
        # exactly this reason, but the MANUAL create path had no such
        # guard, so the same vehicle could be queued twice by hand.
        if order_category == "MAINTENANCE" and maintenance_type_id:
            existing_open = (MaintenanceOrder.query
                            .filter_by(vehicle_id=vehicle_id,
                                      maintenance_type_id=maintenance_type_id)
                            .filter(MaintenanceOrder.status.notin_(
                                ["COMPLETED", "CANCELLED"]))
                            .first())
            if existing_open:
                raise InvalidOrderStateError(
                    f"This vehicle already has an open "
                    f"{existing_open.maintenance_type.name if existing_open.maintenance_type else 'maintenance'} "
                    f"order ({existing_open.document_number or 'draft'}, "
                    f"status {existing_open.status}). Complete or cancel it "
                    f"before creating another one.")
        # Snapshot the vehicle's CURRENT branch as the "From" for the
        # Asset Transfer Report -- must be captured now, at creation,
        # since completion will update Vehicle.branch_id to the
        # destination, and by then the vehicle's current branch would
        # already be the new one, not the "From" branch being reported.
        origin_branch_id = (vehicle.branch_id if destination_branch_id
                           and vehicle else None)
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
            template = db.session.get(PMScopeTemplate, scope_template_id)
            for item in template.items:
                order.checklist_items.append(MaintenanceChecklistItem(
                    activity_code=item.activity_code,
                    activity_description=item.activity_description,
                    sort_order=item.sort_order))

        db.session.commit()
        return order

    def editable_scope(self, order) -> str:
        """How much of this order may be edited right now.

        Returns "FULL", "REMARKS_ONLY", or "NONE".

        "DRAFT" alone is not enough to decide: in this system an order
        stays DRAFT while its approval is PENDING, so DRAFT covers three
        genuinely different situations.

          * DRAFT, never submitted        -> FULL. Nobody has seen it.
          * DRAFT, approval RETURNED      -> FULL. Being able to correct
            it is the entire point of a return.
          * DRAFT, approval PENDING       -> NONE. An approver is looking
            at it right now; silently changing the particulars underneath
            them would mean they approve something other than what they
            reviewed.
          * IN_PROGRESS                   -> REMARKS_ONLY. The order is
            approved and work has started, so the approved particulars
            (vehicle, type, scope, cost) must not move -- but adding
            notes about the work as it happens is exactly what was asked
            for, and is safe.
          * COMPLETED / CANCELLED         -> NONE. Closed records stay
            as they were; they feed cost reports and the audit trail.
        """
        if order is None:
            return "NONE"
        if order.status == "IN_PROGRESS":
            return "REMARKS_ONLY"
        if order.status != "DRAFT":
            return "NONE"
        instance = order.approval_instance
        if instance is None:
            # Submit stores approval_instance_id, but a stale session or
            # an instance created only on the approval table must still
            # lock the order. Look up the latest instance by reference.
            from app.core.approval.models import ApprovalInstance
            instance = (ApprovalInstance.query
                        .filter_by(reference_table="maintenance_orders",
                                   reference_id=order.id)
                        .order_by(ApprovalInstance.id.desc())
                        .first())
        if instance is None or instance.status in ("RETURNED", "CANCELLED"):
            return "FULL"
        # PENDING / APPROVED (awaiting Start work) — particulars stay frozen
        # until an approver Returns the request.
        if instance.status == "PENDING":
            return "NONE"
        return "NONE"

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
            instance = order.approval_instance
            if order.status == "DRAFT" and instance is not None:
                raise InvalidOrderStateError(
                    "This order is awaiting approval and cannot be edited. "
                    "Ask the approver to return it if it needs changes.")
            raise InvalidOrderStateError(
                f"A {order.status} order can no longer be edited.")

        if scope == "REMARKS_ONLY":
            allowed = {"description"}
            rejected = sorted(k for k in fields
                             if k not in allowed and fields[k] is not None)
            if rejected:
                raise InvalidOrderStateError(
                    "Work has already started on this order, so only the "
                    "description/remarks can be changed. Cannot change: "
                    + ", ".join(rejected) + ".")
            fields = {k: v for k, v in fields.items() if k in allowed}

        editable = {
            "description", "scheduled_date", "odometer_at_service",
            "estimated_cost", "assigned_mechanic", "vendor_id",
            "maintenance_type_id", "transaction_type_id",
            "scope_template_id", "pm_schedule_id", "driver_id",
            "destination_branch_id", "assignment_classification",
            "disposal_value", "disposal_recipient",
        }
        for key, value in fields.items():
            if key in editable:
                setattr(order, key, value)
        order.updated_by = user.id if user else None
        db.session.commit()
        return order

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
        if self.editable_scope(order) != "FULL":
            raise InvalidOrderStateError(
                "This order is awaiting approval and cannot be edited. "
                "Ask the approver to return it if parts need to change.")
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

    def remove_part(self, part_id):
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrderPart)
        part = db.session.get(MaintenanceOrderPart, part_id)
        if part is None:
            return None
        order = part.order
        if self.editable_scope(order) != "FULL":
            raise InvalidOrderStateError(
                "This order is awaiting approval and cannot be edited. "
                "Ask the approver to return it if parts need to change.")
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
