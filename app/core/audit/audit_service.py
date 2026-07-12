"""Automatic audit trail via SQLAlchemy session events.

register_audit_listeners() hooks before_flush and after_flush; every
insert/update/delete on models inheriting BaseModel is logged without any
per-module code. Values are serialised to JSON-safe primitives.
"""
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import event, inspect

from app.extensions import db
from app.core.models.audit_log import AuditLog

_EXCLUDED_TABLES = {"audit_logs"}
_registered = False


def _current_user_id():
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return current_user.id
    except Exception:
        pass
    return None


def _current_ip():
    try:
        from flask import request, has_request_context
        if has_request_context():
            return request.remote_addr
    except Exception:
        pass
    return None


def _serialise(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _row_values(obj):
    mapper = inspect(obj).mapper
    return {c.key: _serialise(getattr(obj, c.key))
            for c in mapper.column_attrs}


def _changed_values(obj):
    state = inspect(obj)
    old, new = {}, {}
    for attr in state.mapper.column_attrs:
        hist = state.attrs[attr.key].load_history()
        if hist.has_changes():
            old[attr.key] = _serialise(hist.deleted[0]) if hist.deleted else None
            new[attr.key] = _serialise(hist.added[0]) if hist.added else None
    return old, new


def register_audit_listeners():
    global _registered
    if _registered:
        return
    _registered = True

    @event.listens_for(db.session.__class__, "before_flush")
    def _before_flush(session, flush_context, instances):
        entries = []
        uid, ip = _current_user_id(), _current_ip()
        for obj in session.new:
            if obj.__tablename__ in _EXCLUDED_TABLES or isinstance(obj, AuditLog):
                continue
            # Auto-populate created_by/updated_by on every model that has
            # these columns (BaseModel) — zero per-module code, same
            # cross-cutting pattern as the audit log itself.
            if hasattr(obj, "created_by") and obj.created_by is None:
                obj.created_by = uid
            if hasattr(obj, "updated_by") and obj.updated_by is None:
                obj.updated_by = uid
            entries.append(AuditLog(table_name=obj.__tablename__, action="CREATE",
                                    new_values=_row_values(obj),
                                    user_id=uid, ip_address=ip))
            session.info.setdefault("_audit_pending_new", []).append((entries[-1], obj))
        for obj in session.dirty:
            if obj.__tablename__ in _EXCLUDED_TABLES or isinstance(obj, AuditLog):
                continue
            if not session.is_modified(obj, include_collections=False):
                continue
            if hasattr(obj, "updated_by"):
                obj.updated_by = uid
            old, new = _changed_values(obj)
            entries.append(AuditLog(table_name=obj.__tablename__, action="UPDATE",
                                    record_id=obj.id, old_values=old,
                                    new_values=new, user_id=uid, ip_address=ip))
        for obj in session.deleted:
            if obj.__tablename__ in _EXCLUDED_TABLES or isinstance(obj, AuditLog):
                continue
            entries.append(AuditLog(table_name=obj.__tablename__, action="DELETE",
                                    record_id=obj.id, old_values=_row_values(obj),
                                    user_id=uid, ip_address=ip))
        session.add_all(entries)

    @event.listens_for(db.session.__class__, "after_flush")
    def _after_flush(session, flush_context):
        # Backfill record_id for CREATE logs now that ids are assigned.
        for log, obj in session.info.pop("_audit_pending_new", []):
            log.record_id = obj.id
