"""Automatic audit trail via SQLAlchemy session events.

register_audit_listeners() hooks before_flush and after_flush_postexec;
every insert/update/delete on models inheriting BaseModel is logged
without any per-module code. Values are serialised to JSON-safe
primitives.
"""
from datetime import datetime, date, time
from decimal import Decimal

from sqlalchemy import event, inspect

from app.extensions import db
from app.core.models.audit_log import AuditLog

# user_preferences: personal display state (chart type...), not a business
# record -- auditing it would add a row per chart click and bury history.
_EXCLUDED_TABLES = {"audit_logs", "user_preferences"}
_registered = False


def _current_user_id():
    # The React app authenticates via a JWT bearer token
    # (app/modules/api/auth.py: api_auth_required), never through
    # Flask-Login's session login_user() -- so flask_login.current_user
    # is always the Anonymous user on that path. api_auth_required
    # stashes the resolved user on flask.g for exactly this reason;
    # check it first so every write made through the web app (not just
    # the Jinja/session screens) gets a real actor on created_by/
    # updated_by and on the AuditLog row itself (KI-21).
    try:
        from flask import g, has_request_context
        if has_request_context():
            api_user = getattr(g, "fms_api_user", None)
            if api_user is not None:
                return api_user.id
    except Exception:
        pass
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
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        # Raw binary (e.g. Attachment.file_data) isn't meaningful in an
        # audit trail and isn't JSON-serializable — record its size
        # instead of the bytes themselves. Handled generically here
        # rather than special-cased per model, so any future
        # LargeBinary/BLOB column added anywhere in the app is safe by
        # default instead of crashing the flush the first time someone
        # saves a row containing one.
        return f"<binary data, {len(value)} bytes>"
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

    @event.listens_for(db.session.__class__, "after_flush_postexec")
    def _after_flush_postexec(session, flush_context):
        # Backfill record_id for CREATE logs now that ids are assigned.
        #
        # This MUST be a direct Core UPDATE via session.connection(), not
        # a plain `log.record_id = obj.id` attribute assignment. SQLAlchemy
        # does not persist attribute changes made inside after_flush (or
        # after_flush_postexec) to the database -- they're silently lost
        # once this flush's unit of work has already completed, and would
        # only reach the DB on some later flush that happens to touch the
        # same object (which, for a row nothing else modifies, is never).
        # The previous version of this code made exactly that mistake:
        # every single CREATE audit row had record_id = NULL, system-wide
        # -- found while fixing KI-21, not itself one of the 28 catalogued
        # issues. Confirmed with a minimal SQLAlchemy repro outside this
        # codebase before changing this.
        #
        # Issuing the UPDATE here, inside after_flush_postexec, still
        # lands in the SAME database transaction as the rest of this
        # flush/commit -- it's a normal statement on the active
        # connection, just issued by hand instead of through the ORM's
        # unit of work.
        pending = session.info.pop("_audit_pending_new", [])
        if not pending:
            return
        table = AuditLog.__table__
        connection = session.connection()
        for log, obj in pending:
            connection.execute(
                table.update().where(table.c.id == log.id)
                             .values(record_id=obj.id))
            # Keep the in-memory object consistent with what's now in the
            # database, for any code in this same request/session that
            # reads log.record_id before the session is reloaded. This
            # does NOT go through the ORM's dirty-tracking (we bypassed
            # it on purpose above), so it does not trigger a redundant
            # second UPDATE on the next flush.
            log.record_id = obj.id
