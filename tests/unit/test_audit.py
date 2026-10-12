from app.core.models.audit_log import AuditLog
from app.modules.user_management.models import Role


def test_insert_writes_audit_row(db):
    db.session.add(Role(name="Auditors"))
    db.session.commit()
    log = AuditLog.query.filter_by(table_name="roles", action="CREATE").first()
    assert log is not None
    assert log.new_values["name"] == "Auditors"


def test_update_writes_old_and_new(db):
    r = Role(name="Before")
    db.session.add(r)
    db.session.commit()
    r.name = "After"
    db.session.commit()
    log = AuditLog.query.filter_by(table_name="roles", action="UPDATE").first()
    assert log.old_values["name"] == "Before"
    assert log.new_values["name"] == "After"


def test_audit_log_itself_not_audited(db):
    db.session.add(Role(name="R"))
    db.session.commit()
    assert AuditLog.query.filter_by(table_name="audit_logs").count() == 0


def test_create_log_records_which_row_was_created(db):
    """record_id must point at the row the CREATE log describes.

    The backfill runs in SQLAlchemy's after_flush hook, after the new
    row has an id but before that id is known anywhere else. A plain
    Python attribute assignment there is silently lost (SQLAlchemy does
    not persist after_flush mutations), which left record_id NULL for
    every CREATE action in the system -- discovered while fixing KI-21,
    not one of the 28 catalogued issues.
    """
    r = Role(name="RecordIdCheck")
    db.session.add(r)
    db.session.commit()
    log = AuditLog.query.filter_by(table_name="roles", action="CREATE") \
        .order_by(AuditLog.id.desc()).first()
    assert log is not None
    assert log.record_id == r.id

    # Confirmed by a fresh query too, not just the in-memory object the
    # event handler mutated -- the earlier bug set the Python attribute
    # but never wrote it to the database.
    db.session.expire(log)
    reloaded = db.session.get(AuditLog, log.id)
    assert reloaded.record_id == r.id
