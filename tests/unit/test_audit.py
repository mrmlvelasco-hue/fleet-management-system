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
