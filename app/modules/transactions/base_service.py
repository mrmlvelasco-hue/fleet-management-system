"""Shared base for transaction module services.

Every transaction module (Trip Ticket, ATD, Vehicle Movement, and the
7 more in 3b/3c) follows the same submit/approve/reject/return/cancel
recipe: create a DRAFT record, submit it through the generic ApprovalEngine
(no approval logic here), and keep only the module's own *physical*
status (DRAFT/RELEASED/COMPLETED/etc.) in sync — the approval workflow
status itself lives entirely on the ApprovalInstance.
"""
from datetime import date as _date_cls, datetime as _datetime_cls

from app.extensions import db
from app.core.approval.engine import ApprovalEngine
from sqlalchemy.orm import joinedload


def _as_date(value):
    """Coerce whatever arrived as the end of a range to a plain date.

    The filter bar submits a string, tests pass a date, and a caller may
    pass a datetime -- all three have to end up comparable so the
    end-of-day arithmetic below behaves identically for each.
    """
    if isinstance(value, _datetime_cls):
        return value.date()
    if isinstance(value, _date_cls):
        return value
    return _datetime_cls.strptime(str(value)[:10], "%Y-%m-%d").date()


class NotVisibleError(Exception):
    """Raised when a user attempts to act on a record outside their
    organizational scope and who isn't its own requester. Distinct from
    NotEligibleApproverError (which governs approval actions specifically,
    already enforced by the ApprovalEngine's own eligibility check) — this
    guards the actions that had no scope protection at all: submit,
    resubmit, cancel."""
    pass


def naive_utc(dt):
    """Audit and approval timestamps are written timezone-aware (UTC)
    but SQLite hands them back naive; compare them all as naive UTC."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


#: id field -> (module, model, how to show a row). Used to turn the ids
#: recorded in the Audit Trail back into names a person recognises.
_ID_DISPLAY = {
    "vehicle_id": ("app.modules.master_data.vehicle.models", "Vehicle",
                   lambda r: r.plate_number or r.conduction_number),
    "driver_id": ("app.modules.master_data.driver.models", "Driver",
                  lambda r: f"{r.first_name} {r.last_name}".strip()),
    "vendor_id": ("app.modules.master_data.vendor.models", "Vendor",
                  lambda r: r.name),
    "department_id": ("app.modules.master_data.org.models", "Department",
                      lambda r: r.name),
    "destination_branch_id": ("app.modules.master_data.org.models",
                              "Branch", lambda r: r.name),
    "tire_id": ("app.modules.master_data.tire.models", "Tire",
                lambda r: r.serial_number),
    "battery_id": ("app.modules.master_data.battery.models", "Battery",
                   lambda r: r.serial_number),
    "transaction_type_id": (
        "app.modules.transactions.maintenance_order.models",
        "TransactionType", lambda r: r.name),
    "maintenance_type_id": ("app.modules.master_data.reference.models",
                            "MaintenanceType", lambda r: r.name),
    "scope_template_id": ("app.modules.maintenance_config.models",
                          "PMScopeTemplate", lambda r: r.name),
    "maintenance_order_id": (
        "app.modules.transactions.maintenance_order.models",
        "MaintenanceOrder", lambda r: r.document_number or f"MO #{r.id}"),
}


def change_display(field, kind, value):
    """An audit value as a person reads it: names instead of ids, money
    with separators. Never raises -- a record deleted since the change
    still shows its id rather than breaking the list."""
    if value in (None, ""):
        return "—"
    try:
        if kind == "money":
            from decimal import Decimal
            return f"{Decimal(str(value)):,.2f}"
        if field in _ID_DISPLAY:
            import importlib
            module, cls, show = _ID_DISPLAY[field]
            model = getattr(importlib.import_module(module), cls)
            row = db.session.get(model, int(value))
            return show(row) if row else f"#{value}"
    except Exception:
        pass
    return str(value)


class DocumentStateError(Exception):
    """The document is closed for editing (left Draft, completed,
    cancelled or rejected). API: 409."""


class DocumentEditForbiddenError(Exception):
    """The user may not edit this document: neither its initiator nor a
    holder of <document>.update. API: 403."""


class DocumentFieldError(ValueError):
    """A value the document cannot take; carries the field name so the
    API can return a field-named 400."""

    def __init__(self, message, field="_"):
        super().__init__(message)
        self.field = field


class BaseTransactionService:
    model = None              # subclass sets: the SQLAlchemy model
    document_type_code = None  # subclass sets: e.g. "TT", "ATD", "VM"
    reference_table = None    # subclass sets: e.g. "trip_tickets"

    def __init__(self):
        self.engine = ApprovalEngine()

    def _infer_branch_id(self, record):
        """Best-effort organizational context for F1 org-scoped approval:
        checks common attribute paths so most transaction modules get
        branch-scoped approval eligibility for free, with no code changes
        of their own. Returns None if none of these paths apply — the
        engine then falls back to role-only eligibility (unchanged
        behavior) for that instance."""
        for path in ("vehicle.branch_id", "branch_id", "department.branch_id"):
            obj = record
            try:
                for attr in path.split("."):
                    obj = getattr(obj, attr)
                if obj is not None:
                    return obj
            except AttributeError:
                continue
        return None

    def _infer_company_id(self, record):
        """Stage 3B (D4/D7) equivalent of _infer_branch_id: best-effort
        Company context for ORG_COMPANY/ORG_REGION/ORG_AREA_DISTRICT
        approval eligibility, with the same no-code-change-per-module and
        backward-compatible-None-fallback guarantees."""
        for path in ("vehicle.company_id", "company_id"):
            obj = record
            try:
                for attr in path.split("."):
                    obj = getattr(obj, attr)
                if obj is not None:
                    return obj
            except AttributeError:
                continue
        return None

    # Field names each module uses for the value the Approval Matrix is
    # banded on. Checked in order, first non-None wins.
    #
    # This exists because the previous code was a bare
    # getattr(record, "amount", None) -- and MaintenanceOrder has NO
    # `amount` column at all (it has estimated_cost / actual_cost). Only
    # PurchaseRequest actually defines `amount`. So every MO resolved
    # its matrix with amount=None, which in ApprovalMatrixService.resolve
    # matches ONLY a matrix whose min_amount AND max_amount are both
    # NULL. A correctly configured matrix with real bands (0-49,000 and
    # 50,000-20,000,000) therefore matched nothing and raised
    # NoMatrixError -- the configuration was never the problem.
    APPROVAL_AMOUNT_FIELDS = ("amount", "estimated_cost", "actual_cost",
                              "total_amount")

    def _approval_amount(self, record):
        """The monetary value this record's approval routing is based on.

        Falls back through the known field names rather than assuming
        one, so a module that names its money column differently routes
        correctly instead of silently resolving with None.
        """
        from decimal import Decimal
        for field in self.APPROVAL_AMOUNT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                return value
        return Decimal("0")

    def submit(self, record_id: int, user):
        """Submit a DRAFT record through the Approval Engine."""
        record = db.session.get(self.model, record_id)
        if user is not None and not self._visible_to(record, user):
            raise NotVisibleError(
                "You do not have access to this record.")
        # Make sure the record HAS its document number before the
        # approval task takes its denormalised copy. Numbering normally
        # happens at create time, but it's written defensively there
        # (a numbering-scheme failure must not block creating the
        # record), so a record can reach submit with no number -- and
        # the approval task would then carry a null number for the rest
        # of its life, showing as "(no number)" in every approver's
        # worklist even after the document itself had one.
        if not getattr(record, "document_number", None) \
                and hasattr(record, "document_number"):
            try:
                from app.core.numbering.numbering_service import (
                    AutoNumberingService)
                record.document_number = AutoNumberingService().generate(
                    self.document_type_code)
                db.session.commit()
            except Exception:
                # Still must not block submission -- the worklist now
                # resolves the live number as a fallback anyway.
                db.session.rollback()
        instance = self.engine.submit(
            self.document_type_code, self.reference_table, record_id,
            amount=self._approval_amount(record), user=user,
            branch_id=self._infer_branch_id(record),
            company_id=self._infer_company_id(record),
            document_number=getattr(record, "document_number", None))
        record.approval_instance_id = instance.id
        db.session.commit()
        return record

    def approve(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        self.engine.approve(record.approval_instance, user, remarks)
        db.session.commit()
        return record

    def reject(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        self.engine.reject(record.approval_instance, user, remarks)
        db.session.commit()
        return record

    def return_document(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        self.engine.return_document(record.approval_instance, user, remarks)
        db.session.commit()
        return record

    def resubmit(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        if user is not None and not self._visible_to(record, user):
            raise NotVisibleError(
                "You do not have access to this record.")
        inst = record.approval_instance
        if inst is not None and inst.branch_id is None:
            inst.branch_id = self._infer_branch_id(record)
        if inst is not None and inst.company_id is None:
            inst.company_id = self._infer_company_id(record)
        self.engine.resubmit(inst, user, remarks)
        db.session.commit()
        return record

    def cancel(self, record_id: int, user, remarks=None):
        record = db.session.get(self.model, record_id)
        if user is not None and not self._visible_to(record, user):
            raise NotVisibleError(
                "You do not have access to this record.")
        if record.approval_instance_id:
            self.engine.cancel(record.approval_instance, user, remarks)
        record.status = "CANCELLED"
        db.session.commit()
        return record

    # ── Edit policy (2026-10-12; spec document-edit-policy-design) ───
    #
    # One rule for every request document -- the Maintenance Order rule
    # (2026-10-13: "same approval path, standard process"): open while
    # its physical status is in EDITABLE_STATUSES (Draft -- unsubmitted,
    # Pending, Returned or Approved -- plus the document's ongoing work
    # status: MO In Progress, PR Ordered, Trip Ticket Released, ATD
    # Active, Movement In Transit) and its approval is not Rejected;
    # locked once closed (Completed, Received, Cancelled, Expired...);
    # editable by the initiator or a holder of update_permission. Approval is NOT restarted by an edit; the
    # people it concerns are notified and the change is listed.

    #: Physical statuses in which the document may still be edited.
    EDITABLE_STATUSES = ("DRAFT",)
    #: e.g. "tripticket.update" -- the non-initiator edit permission.
    update_permission = None
    #: field -> (label, kind). kind: id | int | money | date | datetime |
    #: text. The edit allow-list AND the changed-after-submission labels.
    EDIT_FIELDS: dict = {}
    #: field -> (label, kind) shown in changes_since_submission but NOT
    #: editable -- derived values a person still needs to see move
    #: (e.g. a Purchase Request total recomputed from its lines).
    TRACKED_FIELDS: dict = {}

    def _latest_instance(self, record):
        """The record's approval instance; falls back to the latest
        instance by reference so a stale session still sees it."""
        instance = getattr(record, "approval_instance", None)
        if instance is None:
            from app.core.approval.models import ApprovalInstance
            instance = (ApprovalInstance.query
                        .filter_by(reference_table=self.reference_table,
                                   reference_id=record.id)
                        .order_by(ApprovalInstance.id.desc()).first())
        return instance

    def editable_scope(self, record) -> str:
        """"FULL" while open, "NONE" once closed."""
        if record is None or record.status not in self.EDITABLE_STATUSES:
            return "NONE"
        instance = self._latest_instance(record)
        if instance is not None and instance.status == "REJECTED":
            return "NONE"
        return "FULL"

    def can_edit(self, record, user) -> bool:
        """May THIS user edit THIS record now? Open, and the user is the
        initiator or holds update_permission."""
        if user is None or self.editable_scope(record) != "FULL":
            return False
        if getattr(record, "requested_by", None) == user.id:
            return True
        return bool(self.update_permission
                    and user.has_permission(self.update_permission))

    def _validate_edit(self, record, fields) -> None:
        """Hook: raise DocumentFieldError for a value this document's
        create() would also have refused. Runs before anything is set."""

    def _after_edit(self, record, fields) -> None:
        """Hook: keep derived values in step (e.g. an expiry date)."""

    def update(self, record_id: int, *, user=None, **fields):
        """Edit an open document within EDIT_FIELDS.

        State is checked before permission: a closed document is closed
        for everybody (409); the permission answer (403) only means
        something while it is open. Unknown keys are ignored, so status,
        requested_by and the like can never be set through an edit."""
        record = db.session.get(self.model, record_id)
        if record is None:
            raise DocumentStateError("Document not found.")
        if self.editable_scope(record) != "FULL":
            instance = self._latest_instance(record)
            if instance is not None and instance.status == "REJECTED":
                raise DocumentStateError(
                    "This document was rejected and can no longer be edited.")
            raise DocumentStateError(
                f"A {record.status} document can no longer be edited.")
        if user is not None and not self.can_edit(record, user):
            raise DocumentEditForbiddenError(
                "Only the person who raised this document, or a user with "
                "permission to update it, can edit it.")
        fields = {k: v for k, v in fields.items() if k in self.EDIT_FIELDS}
        self._validate_edit(record, fields)
        # By value: the edit form re-posts every field, and
        # Decimal("1500.00") == 1500 is not a change.
        changed = [k for k, v in fields.items() if getattr(record, k) != v]
        for key, value in fields.items():
            setattr(record, key, value)
        self._after_edit(record, fields)
        if user is not None and hasattr(record, "updated_by"):
            record.updated_by = user.id
        db.session.commit()
        if changed:
            self._notify_edit_after_submission(record)
        return record

    def _notify_edit_after_submission(self, record) -> None:
        """Tell the approvers through the Notification Engine's
        configurable rules (seeded: current approver + prior approvers)
        -- once someone other than the initiator is relying on the
        document: approval PENDING or APPROVED, or work under way (any
        status past DRAFT: In Progress, Ordered, Released, Active...).
        A Returned document is being fixed at the approver's request."""
        instance = self._latest_instance(record)
        if instance is None:
            return
        if not (instance.status in ("PENDING", "APPROVED")
                or record.status != "DRAFT"):
            return
        from app.core.approval.engine import publish
        publish("edited_after_submission", instance)

    def changes_since_submission(self, record) -> list:
        """Edits made after the document was first submitted, newest
        first: [{field, label, old, new, changed_by, changed_at}]. Read
        from the Audit Trail (every field change is already logged with
        who and when); only EDIT_FIELDS count, so status moves and
        bookkeeping columns never show as edits."""
        from app.core.approval.models import ApprovalAction, ApprovalInstance
        from app.core.models.audit_log import AuditLog
        from app.modules.user_management.models import User
        instance_ids = [i.id for i in ApprovalInstance.query.filter_by(
            reference_table=self.reference_table, reference_id=record.id)]
        if not instance_ids:
            return []
        first = (ApprovalAction.query
                 .filter(ApprovalAction.instance_id.in_(instance_ids),
                         ApprovalAction.action == "SUBMIT")
                 .order_by(ApprovalAction.acted_at.asc()).first())
        if first is None:
            return []
        since = naive_utc(first.acted_at)
        rows = (AuditLog.query
                .filter_by(table_name=self.reference_table,
                           record_id=record.id, action="UPDATE")
                .order_by(AuditLog.id.desc()).all())
        users, out = {}, []
        for row in rows:
            if naive_utc(row.timestamp) < since:
                continue
            old, new = row.old_values or {}, row.new_values or {}
            for field, (label, kind) in {**self.EDIT_FIELDS,
                                         **self.TRACKED_FIELDS}.items():
                # The audit listener records only attributes whose value
                # actually changed, so presence is enough.
                if field not in new:
                    continue
                if row.user_id and row.user_id not in users:
                    u = db.session.get(User, row.user_id)
                    users[row.user_id] = u.full_name if u else None
                out.append({
                    "field": field, "label": label,
                    "old": change_display(field, kind, old.get(field)),
                    "new": change_display(field, kind, new.get(field)),
                    "changed_by": users.get(row.user_id),
                    "changed_at": naive_utc(row.timestamp).isoformat(),
                })
        return out

    def _visible_to(self, record, user) -> bool:
        """Does `user` have visibility into this record?
        - No user passed -> no filtering (backward compatible).
        - The record's own requester always sees it, regardless of scope.
        - Anyone who is part of the document's comment thread -- either
          because they posted a comment on it, or because someone
          specifically notified them via the "Type a name to notify"
          picker -- always sees it too. Being addressed in a comment makes
          someone a participant in that document regardless of which
          branch they're normally scoped to; without this, a comment
          recipient outside the document's branch couldn't even open the
          page to read the comment that mentioned them.
        - No org context on the record, or the user has no scope rows
          assigned (not yet opted into org-scoping) -> visible (same
          rollout-safety rule as F1's approval eligibility).
        - Otherwise, visible only if the user's UserOrgScope covers the
          record's inferred branch.
        """
        if user is None:
            return True
        if getattr(record, "requested_by", None) == getattr(user, "id", None):
            return True
        if self._is_comment_participant(record, user):
            return True
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        branch_id = self._infer_branch_id(record)
        return UserOrgScopeService().covers(user.id, branch_id=branch_id)

    def _is_comment_participant(self, record, user) -> bool:
        from app.core.comments.models import DocumentComment
        from sqlalchemy import or_
        return db.session.query(DocumentComment.id).filter(
            DocumentComment.reference_table == self.reference_table,
            DocumentComment.reference_id == record.id,
            or_(DocumentComment.author_id == user.id,
               DocumentComment.recipient_id == user.id)
        ).first() is not None

    # Subclasses override with the relationship names their own list
    # template actually renders per row (e.g. ["vehicle",
    # "maintenance_type"]) -- avoids the classic N+1 query problem where
    # a list page triggers one extra lazy-load query PER ROW for each
    # relationship the template accesses, which multiplies badly under
    # real concurrent traffic (more simultaneous DB connections held for
    # longer, not just more total queries). Empty by default so this is
    # opt-in per module rather than guessing which relations to eager-
    # load for every single transaction type.
    list_eager_load = []

    def list(self, include_inactive: bool = True, user=None):
        """Every record this user may see, newest first.

        Kept for callers that genuinely want the whole set. Visibility
        is applied IN SQL (see _apply_visibility_filter) rather than by
        loading everything and filtering in Python, which is what this
        used to do -- on a large transaction table that meant loading
        every row and, worse, running one extra comment-participant
        query PER ROW.
        """
        query = self._visible_query(include_inactive=include_inactive,
                                   user=user)
        return query.order_by(self.model.id.desc()).all()

    def _visible_query(self, include_inactive: bool = True, user=None):
        """The base query for this model with visibility already applied
        as SQL. Shared by list() and by any paginated/filtered listing,
        so both enforce exactly the same access rules.
        """
        query = db.session.query(self.model)
        if self.list_eager_load:
            query = query.options(
                *[joinedload(getattr(self.model, rel))
                 for rel in self.list_eager_load])
        if not include_inactive:
            query = query.filter(self.model.is_active.is_(True))
        return self._apply_visibility_filter(query, user)

    def _apply_visibility_filter(self, query, user):
        """Translate the _visible_to rules into a SQL WHERE clause.

        Every rule from _visible_to is preserved exactly:

          * No user -> no filtering at all.
          * A user with no UserOrgScope rows is not yet opted into
            org-scoping and still sees everything (the same rollout
            safety rule as before -- turning scoping on must not
            silently hide every existing record from everyone).
          * A user holding a GLOBAL or COMPANY scope sees everything.
          * Otherwise: the record's own requester sees it, OR anyone
            who is part of its comment thread sees it, OR the record's
            inferred branch falls inside the user's scoped branches.

        The comment-participant rule becomes a single EXISTS subquery
        instead of one query per record, which is the change that makes
        this viable at scale.
        """
        if user is None:
            return query

        from sqlalchemy import or_, and_
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)

        scopes = UserOrgScopeService().list_for_user(user.id)
        if not scopes:
            return query   # not opted into org-scoping
        if any(sc.scope_type in ("GLOBAL", "COMPANY") for sc in scopes):
            return query

        branch_ids = [sc.branch_id for sc in scopes
                     if sc.scope_type == "BRANCH" and sc.branch_id is not None]
        bu_ids = [sc.business_unit_id for sc in scopes
                 if sc.scope_type == "BUSINESS_UNIT"
                 and sc.business_unit_id is not None]

        clauses = []

        # The requester always sees their own record.
        if hasattr(self.model, "requested_by"):
            clauses.append(self.model.requested_by == user.id)

        # Anyone in the document's comment thread sees it -- one EXISTS
        # rather than a query per row.
        from app.core.comments.models import DocumentComment
        participant = (
            db.session.query(DocumentComment.id)
            .filter(DocumentComment.reference_table == self.reference_table,
                    DocumentComment.reference_id == self.model.id,
                    or_(DocumentComment.author_id == user.id,
                        DocumentComment.recipient_id == user.id))
            .exists())
        clauses.append(participant)

        branch_clause = self._branch_scope_clause(branch_ids, bu_ids)
        if branch_clause is not None:
            clauses.append(branch_clause)

        # A record whose branch can't be inferred at all has no org
        # context to restrict on, and _visible_to treated that as
        # visible -- preserved here rather than silently hiding it.
        no_context = self._no_org_context_clause()
        if no_context is not None:
            clauses.append(no_context)

        return query.filter(or_(*clauses)) if clauses else query

    # Which date columns this module's list can be filtered and sorted
    # on. The FIRST entry is the default and must be the date the list
    # template actually displays.
    #
    # Declared per module rather than guessed. This used to be an ordered
    # candidate list walked with getattr, and for Trip Ticket, ATD and
    # Vehicle Movement every candidate missed, so it fell through to
    # `created_at` -- the filter ranged and sorted on when the row was
    # typed while the column beside it showed the departure / valid-from
    # / movement date. Nothing raised and nothing looked wrong; the
    # numbers simply didn't tie out against a report. A wrong answer
    # delivered confidently is worse than an error, so the guess is gone.
    #
    # `created_at` is offered everywhere: it is the one date every
    # document has, and for the three modules that display no created
    # date of their own it is the only way to ask "what was entered last
    # week".
    date_fields = (("created_at", "Created Date"),)

    #: Legacy fallback, kept only for any service that hasn't declared
    #: date_fields yet. New modules should declare instead.
    date_field_candidates = ("scheduled_date", "request_date",
                            "transaction_date", "registration_date",
                            "trip_date", "created_at")

    def date_field_choices(self):
        """(code, label) pairs for the filter bar's date-column picker,
        skipping anything the model doesn't actually have so a shared
        declaration can't offer a column that would then be ignored."""
        return [(code, label) for code, label in self.date_fields
                if getattr(self.model, code, None) is not None]

    def default_date_field(self):
        choices = self.date_field_choices()
        if choices:
            return choices[0][0]
        for name in self.date_field_candidates:
            if getattr(self.model, name, None) is not None:
                return name
        return None

    def _resolve_date_field(self, requested):
        """The column to filter on, given what the query string asked
        for. An unrecognised value falls back to the default rather than
        raising -- this arrives straight off a URL, so anyone can put
        anything in it, and a stale bookmark shouldn't 500."""
        valid = {code for code, _ in self.date_field_choices()}
        if requested in valid:
            return requested
        return self.default_date_field()

    def _date_column(self, field=None):
        name = self._resolve_date_field(field) if field is not None \
            else self.default_date_field()
        if name is None:
            return None
        return getattr(self.model, name, None)

    def list_filtered(self, *, user=None, page=1, per_page=25,
                     search=None, status=None, branch_id=None,
                     company_id=None, date_from=None, date_to=None,
                     date_field=None, include_inactive=True,
                     extra_filters=None):
        """One page of records matching the standard transaction filters.

        Deliberately built on _visible_query, the same base list() uses,
        so a filtered listing can never accidentally show something the
        unfiltered one would have hidden -- access rules live in exactly
        one place.

        Every filter is optional and is only applied when the model
        actually has the column, because the transaction models genuinely
        differ (a Purchase Request has no vehicle; only some carry a
        branch of their own). Asking for a filter a model doesn't support
        is ignored rather than raising -- a shared filter bar shouldn't
        break a screen just because one field doesn't apply there.

        `search` matches the document number OR the vehicle's plate /
        conduction number, because those are the two things people
        actually have in hand when looking for a transaction.

        `date_field` picks WHICH date column the range applies to and
        the results are sorted by; see date_fields. Unrecognised values
        fall back to the module's default rather than raising.

        Returns (rows, pagination).
        """
        from sqlalchemy import or_

        query = self._visible_query(include_inactive=include_inactive,
                                   user=user)

        if status and hasattr(self.model, "status"):
            statuses = status if isinstance(status, (list, tuple)) else [status]
            query = query.filter(self.model.status.in_(statuses))

        if branch_id:
            branch_clause = self._branch_scope_clause([int(branch_id)], [])
            if branch_clause is not None:
                query = query.filter(branch_clause)

        if company_id:
            # Stage 3C: one edit here covers EVERY transaction list --
            # Maintenance Order, Purchase Request, Trip Ticket, ATD,
            # Vehicle Movement, Tire/Battery transaction, Maintenance
            # Invoice and Handover all page through list_filtered.
            company_clause = self._company_scope_clause(int(company_id))
            if company_clause is not None:
                query = query.filter(company_clause)

        date_col = self._date_column(date_field)
        if date_col is not None:
            if date_from:
                query = query.filter(date_col >= date_from)
            if date_to:
                # Inclusive of the whole end day.
                #
                # `col <= date_to` only achieves that for a plain DATE
                # column. Against a DATETIME it compares to MIDNIGHT at
                # the START of that day, so a trip departing 08:00 on
                # the 31st was silently excluded from a range ending on
                # the 31st. This stayed hidden because the only list
                # converted so far (Maintenance Orders) filters on
                # scheduled_date, which is a Date -- while every column
                # the remaining eight lists use is a DateTime.
                #
                # `< date_to + 1 day` is correct for both types and
                # avoids depending on a time component that a Date
                # column doesn't have.
                from datetime import timedelta
                query = query.filter(
                    date_col < _as_date(date_to) + timedelta(days=1))

        if search:
            needle = f"%{str(search).strip()}%"
            parts = []
            if hasattr(self.model, "document_number"):
                parts.append(self.model.document_number.ilike(needle))
            if hasattr(self.model, "vehicle_id"):
                from app.modules.master_data.vehicle.models import Vehicle
                parts.append(self.model.vehicle_id.in_(
                    db.session.query(Vehicle.id).filter(or_(
                        Vehicle.plate_number.ilike(needle),
                        Vehicle.conduction_number.ilike(needle)))))
            if parts:
                query = query.filter(or_(*parts))

        if extra_filters:
            for clause in extra_filters:
                if clause is not None:
                    query = query.filter(clause)

        order_col = date_col if date_col is not None else self.model.id
        pagination = (query.order_by(order_col.desc(), self.model.id.desc())
                     .paginate(page=page, per_page=per_page,
                               error_out=False))
        return pagination.items, pagination

    def status_choices(self):
        """Distinct statuses actually present for this model, so the
        filter offers real options rather than a hardcoded list that
        drifts from what the workflow really produces."""
        if not hasattr(self.model, "status"):
            return []
        rows = (db.session.query(self.model.status)
               .distinct().order_by(self.model.status).all())
        return [r[0] for r in rows if r[0]]

    def _branch_scope_clause(self, branch_ids, bu_ids):
        """SQL equivalent of _infer_branch_id's attribute walk
        ("vehicle.branch_id", then "branch_id", then
        "department.branch_id") against the user's scoped branches."""
        if not branch_ids:
            return None
        from sqlalchemy import or_
        parts = []
        if hasattr(self.model, "branch_id"):
            parts.append(self.model.branch_id.in_(branch_ids))
        if hasattr(self.model, "vehicle_id"):
            from app.modules.master_data.vehicle.models import Vehicle
            parts.append(self.model.vehicle_id.in_(
                db.session.query(Vehicle.id)
                .filter(Vehicle.branch_id.in_(branch_ids))))
        if hasattr(self.model, "department_id"):
            from app.modules.master_data.org.models import Department
            parts.append(self.model.department_id.in_(
                db.session.query(Department.id)
                .filter(Department.branch_id.in_(branch_ids))))
        if not parts:
            return None
        return or_(*parts)

    def _company_scope_clause(self, company_id):
        """SQL for "this document belongs to Company X".

        The company twin of _branch_scope_clause, walking the same
        attributes in the same order: the document's own company_id
        (a snapshot taken at submission, per D7), then its vehicle's
        EFFECTIVE company, then its department's.

        Unlike the branch clause this does NOT fall back to including
        records with no company at all: a filter asking for one Company
        that returned every unassigned document as well would overstate
        it, which is the failure mode that made the dashboard read 156
        instead of 2.
        """
        from sqlalchemy import or_
        from app.core.company_resolution import company_filter

        parts = []
        if hasattr(self.model, "company_id"):
            parts.append(self.model.company_id == company_id)
        if hasattr(self.model, "vehicle_id"):
            from app.modules.master_data.vehicle.models import Vehicle
            parts.append(self.model.vehicle_id.in_(
                db.session.query(Vehicle.id)
                .filter(company_filter(Vehicle, company_id))))
        if hasattr(self.model, "department_id"):
            from app.modules.master_data.org.models import Department
            parts.append(self.model.department_id.in_(
                db.session.query(Department.id)
                .filter(Department.company_id == company_id)))
        if not parts:
            return None
        return or_(*parts)

    def _no_org_context_clause(self):
        """True for records with no inferable branch at all. _visible_to
        returned True for these (covers() short-circuits on a null
        branch), so they must stay visible."""
        from sqlalchemy import and_
        parts = []
        if hasattr(self.model, "vehicle_id"):
            parts.append(self.model.vehicle_id.is_(None))
        if hasattr(self.model, "branch_id"):
            parts.append(self.model.branch_id.is_(None))
        if hasattr(self.model, "department_id"):
            parts.append(self.model.department_id.is_(None))
        if not parts:
            return None
        return and_(*parts)

    def get_visible(self, record_id: int, user):
        """Like get(), but returns None if `user` doesn't have visibility
        into this record per organizational scope — used by detail/edit
        routes so direct-URL access respects the same scoping as list()."""
        record = db.session.get(self.model, record_id)
        if record is None or not self._visible_to(record, user):
            return None
        return record

    def get(self, record_id: int):
        return db.session.get(self.model, record_id)
