"""Shared API support for the document edit policy.

Every request document's PATCH endpoint and detail payload go through
here, so the rule reads the same everywhere:

* payload keys are coerced by the service's EDIT_FIELDS kinds
  (id | int | money | date | datetime | text); a bad value is a
  field-named 400 in the existing {"fields": {name: message}} shape;
* DocumentEditForbiddenError -> 403, DocumentFieldError -> 400 on that
  field, DocumentStateError (closed document) -> 409;
* the detail carries editable, can_edit (for THIS viewer) and
  changes_since_submission.

Spec: docs/superpowers/specs/2026-10-12-document-edit-policy-design.md
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import jsonify, request


class EditPayloadError(ValueError):
    def __init__(self, message, field):
        super().__init__(message)
        self.field = field


def _validation(message, field):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field: message}}), 400


def coerce_edit_fields(payload, edit_fields):
    """Typed service fields from a PATCH body. Only keys present are
    returned (a partial PATCH leaves the rest alone); null or "" clears a
    field; keys outside edit_fields are dropped."""
    out = {}
    for key, (label, kind) in edit_fields.items():
        if key not in payload:
            continue
        v = payload[key]
        if v is None or (isinstance(v, str) and v.strip() == ""
                         and kind != "text"):
            out[key] = None
            continue
        try:
            if kind in ("id", "int"):
                n = int(str(v).strip().replace(",", ""))
                if n < 0:
                    raise EditPayloadError(f"{label} cannot be negative.", key)
                out[key] = n
            elif kind == "money":
                out[key] = Decimal(str(v).strip().replace(",", ""))
            elif kind == "date":
                out[key] = date.fromisoformat(str(v)[:10])
            elif kind == "datetime":
                out[key] = datetime.fromisoformat(
                    str(v).strip().replace("Z", "+00:00")).replace(tzinfo=None)
            else:
                out[key] = str(v).strip() or None
        except EditPayloadError:
            raise
        except (TypeError, ValueError, InvalidOperation):
            hint = {"id": "a whole number", "int": "a whole number",
                    "money": "a valid amount", "date": "a date (YYYY-MM-DD)",
                    "datetime": "a date and time"}.get(kind, "valid")
            raise EditPayloadError(f"{label} must be {hint}.", key)
    return out


def edit_info(svc, record, user):
    """editable / can_edit / changes_since_submission for a detail."""
    return {
        "editable": svc.editable_scope(record),
        "can_edit": svc.can_edit(record, user),
        "changes_since_submission": svc.changes_since_submission(record),
    }


def edit_document(svc, record_id, api_user, serialize, not_found):
    """The whole PATCH: visibility, coercion, update, error mapping.

    serialize(record) -> dict is the endpoint's own detail serializer;
    edit_info is merged in so the response is the fresh detail."""
    from app.modules.transactions.base_service import (
        DocumentEditForbiddenError, DocumentFieldError, DocumentStateError)
    if svc.get_visible(record_id, api_user) is None:
        return not_found()
    try:
        fields = coerce_edit_fields(request.get_json(silent=True) or {},
                                    svc.EDIT_FIELDS)
    except EditPayloadError as e:
        return _validation(str(e), e.field)
    try:
        record = svc.update(record_id, user=api_user, **fields)
    except DocumentEditForbiddenError as e:
        return jsonify({"error": "forbidden", "message": str(e)}), 403
    except DocumentFieldError as e:
        return _validation(str(e), e.field)
    except DocumentStateError as e:
        return jsonify({"error": "conflict", "message": str(e)}), 409
    data = serialize(record)
    data.update(edit_info(svc, record, api_user))
    return jsonify(data)


def edit_gate(svc, record_id, api_user, not_found):
    """For endpoints that edit part of a document (e.g. PR lines):
    an error response, or None to proceed. A closed document is left
    for the service to refuse with its own message."""
    record = svc.get_visible(record_id, api_user)
    if record is None:
        return not_found()
    if svc.editable_scope(record) == "FULL" and not svc.can_edit(
            record, api_user):
        return jsonify({"error": "forbidden", "message":
                        "Only the person who raised this document, or a user "
                        "with permission to update it, can edit it."}), 403
    return None
