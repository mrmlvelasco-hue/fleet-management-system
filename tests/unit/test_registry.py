from app.core.security.registry import PermissionRegistry, sync_permissions
from app.modules.user_management.models import Permission


def test_register_and_sync(db):
    reg = PermissionRegistry()
    reg.register("widget.create", "widget", "create", "Create widgets")
    reg.register("widget.view", "widget", "view", "View widgets")
    sync_permissions(reg)
    db.session.commit()
    codes = {p.code for p in Permission.query.all()}
    assert {"widget.create", "widget.view"} <= codes


def test_sync_is_idempotent(db):
    reg = PermissionRegistry()
    reg.register("widget.create", "widget", "create", "Create widgets")
    sync_permissions(reg)
    sync_permissions(reg)
    db.session.commit()
    assert Permission.query.filter_by(code="widget.create").count() == 1
